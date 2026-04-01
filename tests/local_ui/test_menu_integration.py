"""Integration tests for menu navigation, commit flow, and status API."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from api.routes import create_app, set_app_state
from cv_engine.gesture_detector import Gesture
from local_ui.bootstrap import BASELINE_HOME_MENU_ENTRIES
from local_ui.menu_state import MenuState, MenuStateMachine


class FakeImageProcessor:
    def __init__(self):
        self.calls = []

    def render_menu_selection(self, entries, selected_index):
        self.calls.append((tuple(entries), selected_index))
        return b"x" * 15000


class FakeSerialManager:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command):
        self.calls.append(command)

        class _Resp:
            status = type("Status", (), {"value": "ok"})()

        return _Resp()


class FakeAudioPlayer:
    def __init__(self):
        self.calls = []

    def play_file(self, path, blocking=True):
        self.calls.append((path, blocking))


def test_menu_commit_flow_navigate_hold_confirm_calls_render_serial_audio(tmp_path):
    image_processor = FakeImageProcessor()
    serial_manager = FakeSerialManager()
    audio_player = FakeAudioPlayer()
    cue_path = tmp_path / "confirm.mp3"
    cue_path.write_bytes(b"mp3")

    menu = MenuStateMachine(
        menu_entries=BASELINE_HOME_MENU_ENTRIES,
        victory_hold_seconds=0.05,
        debounce_frames=2,
        confidence_threshold=0.7,
        audio_player=audio_player,
        serial_manager=serial_manager,
        image_processor=image_processor,
        confirm_audio_path=str(cue_path),
    )

    # Navigate once with thumbs-up (mapped to previous): index 0 -> 3
    assert not menu.handle_gesture(Gesture.THUMBS_UP, 0.9)
    assert menu.handle_gesture(Gesture.THUMBS_UP, 0.9)
    assert menu.selected_index == 3
    assert menu.state == MenuState.NAVIGATING

    # Start confirming and hold until commit request is raised
    assert menu.handle_gesture(Gesture.PEACE, 0.9)
    assert menu.state == MenuState.CONFIRMING

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and not menu.consume_commit_requested():
        menu.handle_gesture(Gesture.PEACE, 0.9)
        time.sleep(0.01)

    # Commit request should have happened by now
    assert menu.state == MenuState.LOCKED

    # Commit handler executes display + audio and unlocks
    committed = asyncio.run(menu.commit_selection())
    assert committed is True
    assert menu.state == MenuState.NAVIGATING
    assert menu.locked is False

    assert image_processor.calls == [(BASELINE_HOME_MENU_ENTRIES, 3)]
    assert len(serial_manager.calls) == 1
    assert audio_player.calls == [(str(cue_path), False)]


def test_menu_status_api_returns_snapshot_fields():
    menu = MenuStateMachine(
        menu_entries=BASELINE_HOME_MENU_ENTRIES,
        debounce_frames=1,
    )
    set_app_state(menu_state=menu)

    app = create_app()
    client = TestClient(app)

    menu.handle_gesture(Gesture.THUMBS_UP, 0.95)

    response = client.get("/api/local-ui/menu/status")
    assert response.status_code == 200

    payload = response.json()
    assert payload["state"] == "navigating"
    assert payload["selected_index"] == 3
    assert payload["menu_entries"] == list(BASELINE_HOME_MENU_ENTRIES)
    assert payload["locked"] is False
    assert 0.0 <= payload["victory_hold_progress"] <= 1.0
    assert isinstance(payload["timestamp"], float)


def test_menu_status_api_returns_404_when_menu_missing():
    set_app_state(menu_state=None)
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/local-ui/menu/status")
    assert response.status_code == 404
    assert response.json()["detail"] == "menu_state not initialized"
