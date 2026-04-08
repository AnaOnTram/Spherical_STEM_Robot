"""Menu state machine with gesture navigation, Victory hold confirm, and input gating.

Handles:
- Navigation via Thumb Up (previous/up) / Thumb Down (next/down) with debouncing
- Confirmation via Victory gesture held for >=3s
- Cancel/back via Open Palm gesture
- Input gating during display refresh (prevents hidden actions)
- Lock/unlock mechanism to gate input during e-ink updates
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from config import (
    MENU_AUDIO_CUE_PATH,
    MENU_DEBOUNCE_FRAMES,
    MENU_DISPLAY_LOCK_TIMEOUT_SECONDS,
    MENU_GESTURE_CONFIDENCE_THRESHOLD,
    MENU_VICTORY_HOLD_SECONDS,
)
from cv_engine.gesture_detector import Gesture

if TYPE_CHECKING:
    from local_ui.arbitration import ArbitrationController

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
    victory_hold_elapsed: float  # seconds
    timestamp: float


class MenuStateMachine:
    """Menu state machine with gesture navigation and confirmation."""

    def __init__(
        self,
        menu_entries: tuple[str, ...],
        victory_hold_seconds: float = MENU_VICTORY_HOLD_SECONDS,
        debounce_frames: int = MENU_DEBOUNCE_FRAMES,
        confidence_threshold: float = MENU_GESTURE_CONFIDENCE_THRESHOLD,
        lock_timeout_seconds: float = MENU_DISPLAY_LOCK_TIMEOUT_SECONDS,
        audio_player: Any = None,
        serial_manager: Any = None,
        image_processor: Any = None,
        arbitration: Optional["ArbitrationController"] = None,
        confirm_audio_path: str = MENU_AUDIO_CUE_PATH,
    ):
        if not menu_entries:
            raise ValueError("menu_entries must contain at least one item")

        self._menu_entries = menu_entries
        self._victory_hold_seconds = victory_hold_seconds
        self._debounce_frames = debounce_frames
        self._confidence_threshold = confidence_threshold
        self._lock_timeout_seconds = lock_timeout_seconds

        self._audio_player = audio_player
        self._serial_manager = serial_manager
        self._image_processor = image_processor
        self._arbitration = arbitration
        self._confirm_audio_path = confirm_audio_path

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

        # Commit trigger tracking
        self._commit_requested = False
        self._navigation_requested = False

    @property
    def state(self) -> MenuState:
        return self._state

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def menu_entries(self) -> tuple[str, ...]:
        return self._menu_entries

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def is_active(self) -> bool:
        """Home menu remains active once initialized."""
        return True

    def consume_commit_requested(self) -> bool:
        """Return and clear commit trigger flag."""
        if not self._commit_requested:
            return False
        self._commit_requested = False
        return True

    def consume_navigation_requested(self) -> bool:
        """Return and clear navigation update flag."""
        if not self._navigation_requested:
            return False
        self._navigation_requested = False
        return True

    def reset_after_external_session(self) -> None:
        """Restore deterministic home-menu interaction state after external session exits."""
        self._commit_requested = False
        self._navigation_requested = False
        self._victory_hold_start = None
        self._debounce_count = 0
        self._debounce_candidate = None
        if self._locked:
            self.unlock()
        if self._state == MenuState.LOCKED:
            self._transition_to(MenuState.NAVIGATING)
        elif self._state == MenuState.COMMITTED:
            self._transition_to(MenuState.NAVIGATING)

    def handle_gesture(self, gesture: Gesture, confidence: float) -> bool:
        """Process an incoming gesture event.

        Returns True if the gesture caused a state transition or action.
        """
        if self._arbitration and not self._arbitration.is_local_allowed():
            logger.debug("menu.gesture_blocked state=%s", self._arbitration.snapshot()["state"])
            return False

        if self._locked and self._lock_start_time is not None:
            elapsed = time.monotonic() - self._lock_start_time
            if elapsed >= self._lock_timeout_seconds:
                logger.warning("menu.auto_unlock lock_timeout_exceeded elapsed=%.2fs", elapsed)
                self.unlock()

        if self._locked:
            return False

        if confidence < self._confidence_threshold:
            if self._victory_hold_start is not None:
                logger.debug(
                    "menu.victory_hold_reset reason=low_confidence confidence=%.2f threshold=%.2f",
                    confidence,
                    self._confidence_threshold,
                )
                self._victory_hold_start = None
                if self._state == MenuState.CONFIRMING:
                    self._transition_to(MenuState.NAVIGATING)
                    return True
            return False

        # UX mapping: "thumbs up" moves selection upward (previous item),
        # and "thumbs down" moves selection downward (next item).
        if gesture == Gesture.THUMBS_UP:
            return self._handle_prev()
        if gesture == Gesture.THUMBS_DOWN:
            return self._handle_next()
        if gesture == Gesture.PEACE:
            return self._handle_victory()
        if gesture == Gesture.OPEN_PALM:
            return self._handle_cancel()

        if self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=different_gesture gesture=%s", gesture.value)
            self._victory_hold_start = None
            if self._state == MenuState.CONFIRMING:
                self._transition_to(MenuState.NAVIGATING)
                return True

        return False

    async def sync_display(self) -> bool:
        """Update e-ink display to reflect current selection (non-committing)."""
        if self._locked:
            return False

        logger.info("menu.sync_display index=%d", self._selected_index)

        try:
            if self._image_processor is not None and self._serial_manager is not None:
                payload = await asyncio.to_thread(
                    self._image_processor.render_menu_selection,
                    self._menu_entries,
                    self._selected_index,
                )

                try:
                    from esp_serial.commands import CommandBuilder

                    command = CommandBuilder.display_image(payload)
                except Exception as exc:
                    logger.warning("menu.sync_display.command_builder_unavailable err=%s", exc)
                    command = payload

                # We don't lock the entire state machine for sync, but we do use the serial manager
                await asyncio.wait_for(
                    self._serial_manager.send_command_async(command),
                    timeout=self._lock_timeout_seconds,
                )
                return True
        except Exception as exc:
            logger.error("menu.sync_display.error err=%s", exc)

        return False

    async def commit_selection(self) -> bool:
        """Commit currently selected menu item and unlock when publish completes."""
        if self._state not in (MenuState.COMMITTED, MenuState.LOCKED):
            return False

        selected_item = self._menu_entries[self._selected_index]
        logger.info("menu.commit_selection.start index=%d item=%s", self._selected_index, selected_item)

        if not self._locked:
            self.lock()
            self._transition_to(MenuState.LOCKED)

        try:
            if self._image_processor is not None and self._serial_manager is not None:
                payload = await asyncio.to_thread(
                    self._image_processor.render_menu_selection,
                    self._menu_entries,
                    self._selected_index,
                )

                try:
                    from esp_serial.commands import CommandBuilder

                    command = CommandBuilder.display_image(payload)
                except Exception as exc:
                    logger.warning("menu.commit_selection.command_builder_unavailable err=%s", exc)
                    command = payload

                await asyncio.wait_for(
                    self._serial_manager.send_command_async(command),
                    timeout=self._lock_timeout_seconds,
                )

            if self._audio_player is not None and Path(self._confirm_audio_path).exists():
                await asyncio.to_thread(
                    self._audio_player.play_file,
                    self._confirm_audio_path,
                    False,
                )
        except asyncio.TimeoutError:
            logger.error("menu.commit_selection.timeout seconds=%.2f", self._lock_timeout_seconds)
        except Exception as exc:
            logger.error("menu.commit_selection.error err=%s", exc)
        finally:
            self.unlock()
            logger.info("menu.commit_selection.done index=%d item=%s", self._selected_index, selected_item)

        return True

    def lock(self) -> None:
        if not self._locked:
            self._locked = True
            self._lock_start_time = time.monotonic()
            logger.info("menu.lock input_gating_enabled")

    def unlock(self) -> None:
        if self._locked:
            self._locked = False
            self._lock_start_time = None
            logger.info("menu.unlock input_gating_disabled")
            if self._state == MenuState.LOCKED:
                self._transition_to(MenuState.NAVIGATING)

    def snapshot(self) -> MenuSnapshot:
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

    def _handle_next(self) -> bool:
        if self._debounce_candidate != Gesture.THUMBS_DOWN and self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=navigation_gesture gesture=thumbs_down")
            self._victory_hold_start = None
            if self._state == MenuState.CONFIRMING:
                self._transition_to(MenuState.NAVIGATING)

        if not self._debounce_gesture(Gesture.THUMBS_DOWN):
            return False

        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)

        if self._state == MenuState.NAVIGATING:
            old_index = self._selected_index
            self._selected_index = (self._selected_index + 1) % len(self._menu_entries)
            logger.info(
                "menu.navigate direction=next from=%d to=%d item=%s",
                old_index,
                self._selected_index,
                self._menu_entries[self._selected_index],
            )
            self._navigation_requested = True
            return True

        return False

    def _handle_prev(self) -> bool:
        if self._debounce_candidate != Gesture.THUMBS_UP and self._victory_hold_start is not None:
            logger.debug("menu.victory_hold_reset reason=navigation_gesture gesture=thumbs_up")
            self._victory_hold_start = None
            if self._state == MenuState.CONFIRMING:
                self._transition_to(MenuState.NAVIGATING)

        if not self._debounce_gesture(Gesture.THUMBS_UP):
            return False

        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)

        if self._state == MenuState.NAVIGATING:
            old_index = self._selected_index
            self._selected_index = (self._selected_index - 1) % len(self._menu_entries)
            logger.info(
                "menu.navigate direction=prev from=%d to=%d item=%s",
                old_index,
                self._selected_index,
                self._menu_entries[self._selected_index],
            )
            self._navigation_requested = True
            return True

        return False

    def _handle_victory(self) -> bool:
        if self._state == MenuState.IDLE:
            self._transition_to(MenuState.NAVIGATING)
            return True

        if self._state not in (MenuState.NAVIGATING, MenuState.CONFIRMING):
            return False

        now = time.monotonic()
        if self._victory_hold_start is None:
            self._victory_hold_start = now
            if self._state == MenuState.NAVIGATING:
                self._transition_to(MenuState.CONFIRMING)
            logger.debug("menu.victory_hold_start")
            return True

        elapsed = now - self._victory_hold_start
        if elapsed >= self._victory_hold_seconds:
            self._transition_to(MenuState.COMMITTED)
            logger.info(
                "menu.commit selected_index=%d item=%s hold_duration=%.2fs",
                self._selected_index,
                self._menu_entries[self._selected_index],
                elapsed,
            )
            self._victory_hold_start = None
            self._commit_requested = True
            self.lock()
            self._transition_to(MenuState.LOCKED)
            return True

        if int(elapsed * 2) % 2 == 0:
            progress = elapsed / self._victory_hold_seconds
            logger.debug(
                "menu.victory_hold_progress elapsed=%.2fs progress=%.1f%%",
                elapsed,
                progress * 100,
            )

        return False

    def _handle_cancel(self) -> bool:
        if self._state == MenuState.CONFIRMING or self._victory_hold_start is not None:
            logger.info("menu.cancel reason=open_palm state=%s", self._state.value)
            self._victory_hold_start = None
            if self._state == MenuState.CONFIRMING:
                self._transition_to(MenuState.NAVIGATING)
                return True

        return False

    def _debounce_gesture(self, gesture: Gesture) -> bool:
        if gesture == self._debounce_candidate:
            self._debounce_count += 1
        else:
            self._debounce_candidate = gesture
            self._debounce_count = 1

        if self._debounce_count >= self._debounce_frames:
            self._debounce_count = 0
            self._debounce_candidate = None
            return True

        return False

    def _transition_to(self, new_state: MenuState) -> None:
        if self._state == new_state:
            return

        old_state = self._state.value
        self._state = new_state
        logger.info(
            "menu.state_transition from=%s to=%s selected_index=%d locked=%s",
            old_state,
            new_state.value,
            self._selected_index,
            self._locked,
        )
