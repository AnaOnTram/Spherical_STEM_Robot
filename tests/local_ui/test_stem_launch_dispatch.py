"""Tests for deterministic local STEM launch dispatch from menu commits."""

from __future__ import annotations

import asyncio

from cv_engine.gesture_detector import Gesture
from local_ui.menu_state import MenuStateMachine
from main import SphericalBot


class FakeArbitration:
    def __init__(self, local_allowed: bool = True, reason: str = "local_allowed"):
        self._local_allowed = local_allowed
        self._reason = reason

    def is_local_allowed(self) -> bool:
        return self._local_allowed

    def snapshot(self):
        return {
            "state": "local" if self._local_allowed else "remote",
            "reason": self._reason,
        }


class FakeMenuState:
    def __init__(self, selected: str = "STEM", commit_once: bool = True):
        self.is_active = True
        self.menu_entries = ("STEM", "Chat", "Follow")
        self.selected_index = self.menu_entries.index(selected)
        self._commit_once = commit_once
        self._commit_consumed = False
        self.handle_calls = 0
        self.commit_calls = 0
        self.sync_calls = 0

    def handle_gesture(self, gesture, confidence):
        self.handle_calls += 1
        return True

    def consume_commit_requested(self):
        if self._commit_once and self._commit_consumed:
            return False
        self._commit_consumed = True
        return True

    def consume_navigation_requested(self):
        return False

    async def commit_selection(self):
        self.commit_calls += 1
        return True

    async def sync_display(self):
        self.sync_calls += 1
        return True


class FakeGestureDetector:
    def __init__(self, gestures):
        self._gestures = list(gestures)
        self._use_mediapipe = False

    def detect(self, frame):
        if self._gestures:
            return [self._gestures.pop(0)]
        return []


class FakeGestureEvent:
    def __init__(self, gesture, confidence=0.95, handedness="Right"):
        self.gesture = gesture
        self.confidence = confidence
        self.handedness = handedness
        self.hand_landmarks = None


class FakeVideoEncoder:
    is_running = True

    def __init__(self, bot_ref=None):
        self.calls = 0
        self._bot_ref = bot_ref

    def get_frame(self, timeout=0.5):
        self.calls += 1
        if self.calls == 1:
            return object()
        if self._bot_ref is not None:
            self._bot_ref._running = False
        return None


def _build_bot_for_dispatch(menu_state, arbitration):
    bot = SphericalBot(enable_video=True, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot._running = True
    bot.menu_state = menu_state
    bot.arbitration_controller = arbitration
    bot.video_encoder = FakeVideoEncoder()
    bot.human_tracker = None

    launch_calls = []

    async def fake_launch(selected_item: str):
        launch_calls.append(selected_item)

    bot._handle_local_stem_commit = fake_launch
    bot.gesture_detector = FakeGestureDetector([FakeGestureEvent(Gesture.PEACE)])
    return bot, launch_calls


def test_single_commit_triggers_single_stem_launch():
    menu = FakeMenuState(selected="STEM", commit_once=True)
    arbitration = FakeArbitration(local_allowed=True)
    bot, launch_calls = _build_bot_for_dispatch(menu, arbitration)

    asyncio.run(bot.run_detection_loop())

    assert menu.commit_calls == 1
    assert launch_calls == ["STEM"]


def test_repeated_frames_do_not_duplicate_launch_without_new_commit():
    menu = FakeMenuState(selected="STEM", commit_once=True)
    arbitration = FakeArbitration(local_allowed=True)
    bot, launch_calls = _build_bot_for_dispatch(menu, arbitration)

    asyncio.run(bot.run_detection_loop())
    # Run loop again with no new commit event.
    bot.video_encoder = FakeVideoEncoder()
    bot.gesture_detector = FakeGestureDetector([FakeGestureEvent(Gesture.PEACE)])
    asyncio.run(bot.run_detection_loop())

    assert launch_calls == ["STEM"]
    assert menu.commit_calls == 1


def test_arbitration_block_suppresses_stem_launch():
    menu = FakeMenuState(selected="STEM", commit_once=True)
    arbitration = FakeArbitration(local_allowed=False, reason="remote_control")
    bot, launch_calls = _build_bot_for_dispatch(menu, arbitration)

    asyncio.run(bot.run_detection_loop())

    assert launch_calls == []
    assert menu.commit_calls == 0
