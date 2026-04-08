"""Tests for MenuStateMachine with gesture navigation and confirmation."""
import time

import pytest

from cv_engine.gesture_detector import Gesture
from local_ui.menu_state import MenuState, MenuStateMachine


@pytest.fixture
def menu():
    """Create a MenuStateMachine with default test entries."""
    return MenuStateMachine(
        menu_entries=("STEM", "Chat", "Follow", "Call Parent"),
        victory_hold_seconds=0.5,  # Shorter for tests
        debounce_frames=2,  # Lower for tests
        confidence_threshold=0.7,
    )


class TestMenuConstruction:
    """Test menu initialization and validation."""

    def test_creates_with_valid_entries(self):
        menu = MenuStateMachine(menu_entries=("A", "B", "C"))
        assert menu.state == MenuState.IDLE
        assert menu.selected_index == 0
        assert menu.menu_entries == ("A", "B", "C")
        assert not menu.locked

    def test_rejects_empty_entries(self):
        with pytest.raises(ValueError, match="at least one item"):
            MenuStateMachine(menu_entries=())

    def test_accepts_single_entry(self):
        menu = MenuStateMachine(menu_entries=("Only",))
        assert len(menu.menu_entries) == 1
        assert menu.selected_index == 0


class TestNavigationDebouncing:
    """Test Thumb Up/Down navigation with debouncing."""

    def test_thumb_up_requires_debounce_frames(self, menu):
        # First frame should not move
        result = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.state == MenuState.IDLE
        assert menu.selected_index == 0
        assert not result

        # Second frame (debounce_frames=2) should accept
        result = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.state == MenuState.NAVIGATING
        assert menu.selected_index == 3
        assert result

    def test_thumb_down_requires_debounce_frames(self, menu):
        # Set to previous item first
        menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.selected_index == 3

        # Thumb down once (not enough)
        result = menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        assert menu.selected_index == 3
        assert not result

        # Second time should accept
        result = menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        assert menu.selected_index == 0
        assert result

    def test_different_gesture_resets_debounce(self, menu):
        # Start thumb up
        menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.selected_index == 0

        # Switch to thumb down before completing debounce
        menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        # Should navigate to next item
        assert menu.selected_index == 1

    def test_navigation_wraps_around(self, menu):
        # Navigate forward to last item (index 3)
        for _ in range(2):  # debounce_frames=2 per gesture
            menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        for _ in range(2):  # debounce_frames=2 per gesture
            menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        assert menu.selected_index == 3

        # One more "down/next" should wrap to 0
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        assert menu.selected_index == 0

    def test_navigation_wraps_backwards(self, menu):
        assert menu.selected_index == 0

        # Go up/previous from 0 should wrap to last (3)
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.selected_index == 3


class TestVictoryHold:
    """Test Victory gesture hold timing and confirmation."""

    def test_victory_starts_hold_timer(self, menu):
        # Transition to NAVIGATING first
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.state == MenuState.NAVIGATING

        # First Victory should transition to CONFIRMING
        result = menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING
        assert result

    def test_victory_does_not_request_navigation_refresh(self, menu):
        # Reach NAVIGATING and clear the selection-change refresh request.
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.consume_navigation_requested() is True

        # Starting a Victory hold should not schedule a display refresh.
        assert menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING
        assert menu.consume_navigation_requested() is False

    def test_victory_hold_commits_after_duration(self, menu):
        # Get to CONFIRMING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Hold Victory for required duration (0.5s in test fixture)
        start = time.monotonic()
        while time.monotonic() - start < 0.6:  # Slightly more than 0.5s
            menu.handle_gesture(Gesture.PEACE, confidence=0.9)
            time.sleep(0.05)

        # Should commit and lock
        assert menu.state == MenuState.LOCKED
        assert menu.locked

    def test_low_confidence_resets_hold(self, menu):
        # Start Victory hold
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Low confidence should reset hold
        result = menu.handle_gesture(Gesture.PEACE, confidence=0.5)  # Below 0.7 threshold
        snapshot = menu.snapshot()
        # Timer should be reset (progress back to 0)
        assert snapshot.victory_hold_progress == 0.0
        assert menu.state == MenuState.NAVIGATING
        assert result

    def test_different_gesture_resets_hold(self, menu):
        # Start Victory hold
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Different gesture should reset hold
        menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        snapshot = menu.snapshot()
        assert snapshot.victory_hold_progress == 0.0
        assert menu.state == MenuState.NAVIGATING


class TestOpenPalmCancel:
    """Test Open Palm cancel/back behavior."""

    def test_open_palm_cancels_victory_hold(self, menu):
        # Start Victory hold
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Open Palm should cancel and return to NAVIGATING
        result = menu.handle_gesture(Gesture.OPEN_PALM, confidence=0.9)
        assert menu.state == MenuState.NAVIGATING
        assert result

        # Victory hold should be reset
        snapshot = menu.snapshot()
        assert snapshot.victory_hold_progress == 0.0

    def test_open_palm_resets_hold_in_navigating(self, menu):
        # Get to NAVIGATING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.state == MenuState.NAVIGATING

        # Open Palm in NAVIGATING should just reset hold (no state change)
        result = menu.handle_gesture(Gesture.OPEN_PALM, confidence=0.9)
        # No state change expected
        assert menu.state == MenuState.NAVIGATING
        # No action taken since we weren't confirming
        assert not result


class TestInputGating:
    """Test input gating during locked state."""

    def test_locked_ignores_all_gestures(self, menu):
        # Get to NAVIGATING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.selected_index == 3

        # Lock
        menu.lock()
        assert menu.locked

        # All gestures should be ignored
        for gesture in [Gesture.THUMBS_UP, Gesture.THUMBS_DOWN, Gesture.PEACE, Gesture.OPEN_PALM]:
            result = menu.handle_gesture(gesture, confidence=0.9)
            assert not result
            assert menu.selected_index == 3  # No change

    def test_unlock_returns_to_navigating(self, menu):
        # Get to LOCKED state via commit
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)

        # Hold for commit
        start = time.monotonic()
        while time.monotonic() - start < 0.6:
            menu.handle_gesture(Gesture.PEACE, confidence=0.9)
            time.sleep(0.05)

        assert menu.state == MenuState.LOCKED

        # Unlock should transition to NAVIGATING
        menu.unlock()
        assert menu.state == MenuState.NAVIGATING
        assert not menu.locked

    def test_lock_unlock_cycle(self, menu):
        assert not menu.locked

        menu.lock()
        assert menu.locked

        menu.unlock()
        assert not menu.locked

    def test_auto_unlock_on_timeout(self):
        menu = MenuStateMachine(
            menu_entries=("A", "B"),
            lock_timeout_seconds=0.2,  # Very short for test
        )

        menu.lock()
        assert menu.locked

        # Wait for timeout
        time.sleep(0.3)

        # Next gesture should trigger auto-unlock check
        result = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        # Should have auto-unlocked and processed gesture
        assert not menu.locked


class TestSnapshot:
    """Test snapshot() method for API exposure."""

    def test_snapshot_reflects_current_state(self, menu):
        snapshot = menu.snapshot()
        assert snapshot.state == "idle"
        assert snapshot.selected_index == 0
        assert snapshot.menu_entries == ("STEM", "Chat", "Follow", "Call Parent")
        assert not snapshot.locked
        assert snapshot.victory_hold_progress == 0.0
        assert snapshot.victory_hold_elapsed == 0.0

    def test_snapshot_shows_victory_progress(self, menu):
        # Start Victory hold
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)

        # Check partial progress
        time.sleep(0.2)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)

        snapshot = menu.snapshot()
        assert snapshot.state == "confirming"
        assert 0.3 <= snapshot.victory_hold_progress <= 0.5  # Approximately 0.2s / 0.5s
        assert 0.2 <= snapshot.victory_hold_elapsed <= 0.3

    def test_snapshot_shows_locked_state(self, menu):
        menu.lock()
        snapshot = menu.snapshot()
        assert snapshot.locked

        menu.unlock()
        snapshot = menu.snapshot()
        assert not snapshot.locked


class TestStateTransitions:
    """Test state machine transitions."""

    def test_idle_to_navigating_on_first_gesture(self, menu):
        assert menu.state == MenuState.IDLE

        for _ in range(2):  # Complete debounce
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)

        assert menu.state == MenuState.NAVIGATING

    def test_navigating_to_confirming_on_victory(self, menu):
        # Get to NAVIGATING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)

        # Victory should transition to CONFIRMING
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

    def test_confirming_to_committed_on_hold_complete(self, menu):
        # Get to CONFIRMING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)

        # Hold for commit
        start = time.monotonic()
        while time.monotonic() - start < 0.6:
            menu.handle_gesture(Gesture.PEACE, confidence=0.9)
            time.sleep(0.05)

        # Should be LOCKED after COMMITTED
        assert menu.state == MenuState.LOCKED

    def test_confirming_to_navigating_on_cancel(self, menu):
        # Get to CONFIRMING
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Cancel with Open Palm
        menu.handle_gesture(Gesture.OPEN_PALM, confidence=0.9)
        assert menu.state == MenuState.NAVIGATING

    def test_locked_to_navigating_on_unlock(self, menu):
        menu.lock()
        # Manually transition to LOCKED state
        menu._state = MenuState.LOCKED

        menu.unlock()
        assert menu.state == MenuState.NAVIGATING


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_item_menu_navigation(self):
        menu = MenuStateMachine(menu_entries=("Only",))

        # Navigate next
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        assert menu.selected_index == 0  # Should wrap to self

        # Navigate prev
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_DOWN, confidence=0.9)
        assert menu.selected_index == 0  # Should wrap to self

    def test_rapid_gesture_changes(self, menu):
        # Rapidly change gestures (should not cause crashes)
        gestures = [Gesture.THUMBS_UP, Gesture.THUMBS_DOWN, Gesture.PEACE, Gesture.OPEN_PALM]

        for _ in range(20):
            for gesture in gestures:
                menu.handle_gesture(gesture, confidence=0.9)

        # Should still be in valid state
        assert menu.state in MenuState
        assert 0 <= menu.selected_index < len(menu.menu_entries)

    def test_multiple_lock_unlock_cycles(self, menu):
        for _ in range(5):
            menu.lock()
            assert menu.locked
            menu.unlock()
            assert not menu.locked

    def test_victory_hold_interrupted_by_navigation(self, menu):
        # Start Victory hold
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)
        menu.handle_gesture(Gesture.PEACE, confidence=0.9)
        assert menu.state == MenuState.CONFIRMING

        # Navigate (should reset hold)
        for _ in range(2):
            menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.9)

        snapshot = menu.snapshot()
        assert snapshot.victory_hold_progress == 0.0
