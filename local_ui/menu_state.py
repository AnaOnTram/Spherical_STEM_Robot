"""Menu state machine with gesture navigation, Victory hold confirm, and input gating.

Handles:
- Navigation via Thumb Up (next) / Thumb Down (previous) with debouncing
- Confirmation via Victory gesture held for >=3s
- Cancel/back via Open Palm gesture
- Input gating during display refresh (prevents hidden actions)
- Lock/unlock mechanism to gate input during e-ink updates

State flow:
  IDLE → NAVIGATING (on first gesture)
  NAVIGATING → CONFIRMING (on Victory gesture)
  CONFIRMING → COMMITTED (after 3s hold) | NAVIGATING (on Open Palm or confidence drop)
  COMMITTED → LOCKED (during display update)
  LOCKED → NAVIGATING (after display completes or timeout)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config import (
    MENU_DEBOUNCE_FRAMES,
    MENU_DISPLAY_LOCK_TIMEOUT_SECONDS,
    MENU_GESTURE_CONFIDENCE_THRESHOLD,
    MENU_VICTORY_HOLD_SECONDS,
)
from cv_engine.gesture_detector import Gesture

logger = logging.getLogger(__name__)


class MenuState(Enum):
    """Menu navigation states."""
    IDLE = "idle"
    NAVIGATING = "navigating"
    CONFIRMING = "confirming"
    LOCKED = "locked"
    COMMITTED = "committed"


@dataclass
class MenuSnapshot:
    """Immutable snapshot of menu state for API/status surfaces."""
    state: str
    selected_index: int
    menu_entries: tuple[str, ...]
    locked: bool
    victory_hold_progress: float  # 0.0–1.0
    victory_hold_elapsed: float   # seconds
    timestamp: float


class MenuStateMachine:
    """Menu state machine with gesture navigation and confirmation.

    Lifecycle:
    1. Create instance with menu_entries tuple
    2. Call handle_gesture() for each incoming gesture event
    3. Check state/selected_index to determine when to render
    4. Call lock() before starting display update
    5. Call unlock() after display update completes or on timeout
    6. Use snapshot() to expose current state to API
    """

    def __init__(
        self,
        menu_entries: tuple[str, ...],
        victory_hold_seconds: float = MENU_VICTORY_HOLD_SECONDS,
        debounce_frames: int = MENU_DEBOUNCE_FRAMES,
        confidence_threshold: float = MENU_GESTURE_CONFIDENCE_THRESHOLD,
        lock_timeout_seconds: float = MENU_DISPLAY_LOCK_TIMEOUT_SECONDS,
    ):
        """Initialize menu state machine.

        Args:
            menu_entries: Tuple of menu item labels (must have length >= 1)
            victory_hold_seconds: Duration Victory must be held to commit (default: 3.0)
            debounce_frames: Consecutive frames required to accept navigation (default: 3)
            confidence_threshold: Minimum gesture confidence to accept (default: 0.7)
            lock_timeout_seconds: Max time to stay locked before auto-unlock (default: 12.0)
        """
        if not menu_entries or len(menu_entries) == 0:
            raise ValueError("menu_entries must contain at least one item")

        self._menu_entries = menu_entries
        self._victory_hold_seconds = victory_hold_seconds
        self._debounce_frames = debounce_frames
        self._confidence_threshold = confidence_threshold
        self._lock_timeout_seconds = lock_timeout_seconds

        # Current state
        self._state = MenuState.IDLE
        self._selected_index = 0
        self._locked = False
        self._lock_start_time: Optional[float] = None

        # Victory hold tracking
        self._victory_hold_start: Optional[float] = None

        # Debounce tracking
        self._debounce_count: int = 0
        self._debounce_candidate: Optional[Gesture] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> MenuState:
        """Current menu state."""
        return self._state

    @property
    def selected_index(self) -> int:
        """Currently selected menu item index (0-based)."""
        return self._selected_index

    @property
    def menu_entries(self) -> tuple[str, ...]:
        """Tuple of menu item labels."""
        return self._menu_entries

    @property
    def locked(self) -> bool:
        """Whether input is currently gated."""
        return self._locked

    def handle_gesture(self, gesture: Gesture, confidence: float) -> bool:
        """Process an incoming gesture event.

        Args:
            gesture: Detected gesture from GestureDetector
            confidence: Gesture confidence score (0.0–1.0)

        Returns:
            True if the gesture caused a state transition or action
        """
        # Check lock timeout and auto-unlock if exceeded
        if self._locked and self._lock_start_time is not None:
            elapsed = time.monotonic() - self._lock_start_time
            if elapsed >= self._lock_timeout_seconds:
                logger.warning(
                    "menu.auto_unlock lock_timeout_exceeded elapsed=%.2fs",
                    elapsed
                )
                self.unlock()

        # Input gating: no-op when locked
        if self._locked:
            return False

        # Confidence gating
        if confidence < self._confidence_threshold:
            # Low confidence resets Victory hold
            if self._victory_hold_start is not None:
                logger.debug(
                    "menu.victory_hold_reset reason=low_confidence "
                    "confidence=%.2f threshold=%.2f",
                    confidence, self._confidence_threshold
                )
                self._victory_hold_start = None
            return False

        # Handle by gesture type
        if gesture == Gesture.THUMBS_UP:
            return self._handle_next()
        elif gesture == Gesture.THUMBS_DOWN:
            return self._handle_prev()
        elif gesture == Gesture.PEACE:  # Victory sign
            return self._handle_victory()
        elif gesture == Gesture.OPEN_PALM:
            return self._handle_cancel()

        # Other gestures reset Victory hold
        if self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=different_gesture gesture=%s", gesture.value)
            self._victory_hold_start = None

        return False

    def lock(self) -> None:
        """Lock input to prevent gestures during display update."""
        if not self._locked:
            self._locked = True
            self._lock_start_time = time.monotonic()
            logger.info("menu.lock input_gating_enabled")

    def unlock(self) -> None:
        """Unlock input after display update completes."""
        if self._locked:
            self._locked = False
            self._lock_start_time = None
            logger.info("menu.unlock input_gating_disabled")

            # Transition back to navigating after unlock
            if self._state == MenuState.LOCKED:
                self._transition_to(MenuState.NAVIGATING)

    def snapshot(self) -> MenuSnapshot:
        """Return immutable snapshot of current state for API/status."""
        victory_hold_progress = 0.0
        victory_hold_elapsed = 0.0

        if self._victory_hold_start is not None:
            victory_hold_elapsed = time.monotonic() - self._victory_hold_start
            victory_hold_progress = min(1.0, victory_hold_elapsed / self._victory_hold_seconds)

        return MenuSnapshot(
            state=self._state.value,
            selected_index=self._selected_index,
            menu_entries=self._menu_entries,
            locked=self._locked,
            victory_hold_progress=victory_hold_progress,
            victory_hold_elapsed=victory_hold_elapsed,
            timestamp=time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Internal gesture handlers
    # ------------------------------------------------------------------

    def _handle_next(self) -> bool:
        """Handle Thumb Up (next) gesture with debouncing."""
        # Reset Victory hold immediately when navigation gesture starts
        if self._debounce_candidate != Gesture.THUMBS_UP and self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=navigation_gesture gesture=thumbs_up")
            self._victory_hold_start = None

        if not self._debounce_gesture(Gesture.THUMBS_UP):
            return False

        # Transition to NAVIGATING if in IDLE
        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)

        if self._state == MenuState.NAVIGATING:
            old_index = self._selected_index
            self._selected_index = (self._selected_index + 1) % len(self._menu_entries)
            logger.info(
                "menu.navigate direction=next from=%d to=%d item=%s",
                old_index, self._selected_index,
                self._menu_entries[self._selected_index]
            )
            return True

        return False

    def _handle_prev(self) -> bool:
        """Handle Thumb Down (previous) gesture with debouncing."""
        # Reset Victory hold immediately when navigation gesture starts
        if self._debounce_candidate != Gesture.THUMBS_DOWN and self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=navigation_gesture gesture=thumbs_down")
            self._victory_hold_start = None

        if not self._debounce_gesture(Gesture.THUMBS_DOWN):
            return False

        # Transition to NAVIGATING if in IDLE
        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)

        if self._state == MenuState.NAVIGATING:
            old_index = self._selected_index
            self._selected_index = (self._selected_index - 1) % len(self._menu_entries)
            logger.info(
                "menu.navigate direction=prev from=%d to=%d item=%s",
                old_index, self._selected_index,
                self._menu_entries[self._selected_index]
            )
            return True

        return False

    def _handle_victory(self) -> bool:
        """Handle Victory gesture with hold timing."""
        # Transition to NAVIGATING if in IDLE
        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)
            return True

        if self._state not in (MenuState.NAVIGATING, MenuState.CONFIRMING):
            return False

        # Start Victory hold timer if not already tracking
        now = time.monotonic()
        if self._victory_hold_start is None:
            self._victory_hold_start = now
            if self._state == MenuState.NAVIGATING:
                self._transition_to(MenuState.CONFIRMING)
            logger.debug("menu.victory_hold_start")
            return True

        # Check hold duration
        elapsed = now - self._victory_hold_start
        if elapsed >= self._victory_hold_seconds:
            # Commit!
            self._transition_to(MenuState.COMMITTED)
            logger.info(
                "menu.commit selected_index=%d item=%s hold_duration=%.2fs",
                self._selected_index,
                self._menu_entries[self._selected_index],
                elapsed
            )
            self._victory_hold_start = None

            # Auto-lock after commit (caller should unlock after display update)
            self.lock()
            self._transition_to(MenuState.LOCKED)
            return True

        # Still holding, log progress periodically
        if int(elapsed * 2) % 2 == 0:  # Log every ~0.5s
            progress = elapsed / self._victory_hold_seconds
            logger.debug(
                "menu.victory_hold_progress elapsed=%.2fs progress=%.1f%%",
                elapsed, progress * 100
            )

        return False

    def _handle_cancel(self) -> bool:
        """Handle Open Palm (cancel/back) gesture."""
        # Reset Victory hold timer
        if self._victory_hold_start is not None:
            logger.info("menu.cancel reason=open_palm")
            self._victory_hold_start = None

            # Transition from CONFIRMING back to NAVIGATING
            if self._state == MenuState.CONFIRMING:
                self._transition_to(MenuState.NAVIGATING)
                return True

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _debounce_gesture(self, gesture: Gesture) -> bool:
        """Debounce a navigation gesture.

        Returns True if the gesture has been seen for enough consecutive frames.
        """
        if gesture == self._debounce_candidate:
            self._debounce_count += 1
        else:
            self._debounce_candidate = gesture
            self._debounce_count = 1

        if self._debounce_count >= self._debounce_frames:
            # Reset debounce state after accepting
            self._debounce_count = 0
            self._debounce_candidate = None
            return True

        return False

    def _transition_to(self, new_state: MenuState) -> None:
        """Transition to a new state with logging."""
        if self._state == new_state:
            return

        old_state = self._state.value
        self._state = new_state
        logger.info(
            "menu.state_transition from=%s to=%s selected_index=%d locked=%s",
            old_state, new_state.value, self._selected_index, self._locked
        )
