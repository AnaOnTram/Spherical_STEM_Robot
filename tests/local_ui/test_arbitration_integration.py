"""Integration tests for API-driven arbitration and menu gesture gating."""

from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest
from fastapi.testclient import TestClient

from api.routes import create_app, set_app_state
from cv_engine.gesture_detector import Gesture
from local_ui.arbitration import ArbitrationController
from local_ui.menu_state import MenuStateMachine


class _FakeResponseStatus:
    OK = "ok"


class _FakeResponse:
    def __init__(self, status: str = _FakeResponseStatus.OK, message: str = "ok"):
        self.status = status
        self.message = message


class _FakeCommandBuilder:
    @staticmethod
    def motor_velocity(left_speed: int, right_speed: int, duration_ms: int):
        return {
            "kind": "motor_velocity",
            "left_speed": left_speed,
            "right_speed": right_speed,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def motor_stop():
        return {"kind": "motor_stop"}

    @staticmethod
    def display_image(payload):
        return {"kind": "display_image", "payload": payload}

    @staticmethod
    def display_clear():
        return {"kind": "display_clear"}


class FakeSerialManager:
    def __init__(self, should_raise: bool = False):
        self.should_raise = should_raise
        self.commands = []

    async def send_command_async(self, command):
        self.commands.append(command)
        if self.should_raise:
            raise RuntimeError("serial write failed")
        return _FakeResponse()


class BlockingSerialManager(FakeSerialManager):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    async def send_command_async(self, command):
        self.commands.append(command)
        self.entered.set()
        await asyncio.to_thread(self.release.wait)
        return _FakeResponse()


class FakeImageProcessor:
    def process_text(self, text: str):
        return f"text:{text}".encode("utf-8")

    def create_pattern(self, pattern: str):
        return f"pattern:{pattern}".encode("utf-8")

    def render_lesson(self, question: str, options: list[str], title: str):
        return f"{title}:{question}:{'|'.join(options)}".encode("utf-8")

    def render_remote_active_notice(self, text: str, font_size: int = 32):
        return f"notice:{text}:{font_size}".encode("utf-8")


class FakeWebSocketManager:
    def __init__(self, should_raise: bool = False):
        self.should_raise = should_raise
        self.events: list[dict[str, str]] = []

    async def broadcast_arbitration(self, state: str, reason: str):
        if state is None:
            raise ValueError("state must not be None")
        if self.should_raise:
            raise RuntimeError("broadcast failed")
        self.events.append({"state": state, "reason": reason})


@pytest.fixture(autouse=True)
def fake_esp_serial_modules(monkeypatch):
    """Provide lightweight esp_serial.* modules for endpoint imports in tests."""
    fake_pkg = types.ModuleType("esp_serial")
    fake_commands = types.ModuleType("esp_serial.commands")
    fake_commands.CommandBuilder = _FakeCommandBuilder

    fake_protocol = types.ModuleType("esp_serial.protocol")
    fake_protocol.ResponseStatus = _FakeResponseStatus

    monkeypatch.setitem(sys.modules, "esp_serial", fake_pkg)
    monkeypatch.setitem(sys.modules, "esp_serial.commands", fake_commands)
    monkeypatch.setitem(sys.modules, "esp_serial.protocol", fake_protocol)


@pytest.fixture
def arbitration(ws_manager: FakeWebSocketManager) -> ArbitrationController:
    return ArbitrationController(
        cooldown_seconds=0.05,
        serial_manager=FakeSerialManager(),
        image_processor=FakeImageProcessor(),
        ws_manager=ws_manager,
    )


@pytest.fixture
def ws_manager() -> FakeWebSocketManager:
    return FakeWebSocketManager()


@pytest.fixture
def menu(arbitration: ArbitrationController) -> MenuStateMachine:
    return MenuStateMachine(
        menu_entries=("STEM", "Chat", "Follow", "Call Parent"),
        debounce_frames=1,
        confidence_threshold=0.7,
        arbitration=arbitration,
    )


@pytest.fixture
def app_client(arbitration: ArbitrationController, menu: MenuStateMachine):
    serial = FakeSerialManager()
    set_app_state(
        serial_manager=serial,
        image_processor=FakeImageProcessor(),
        menu_state=menu,
        arbitration=arbitration,
    )
    app = create_app()
    return TestClient(app), serial


def test_remote_movement_preempts_menu_during_inflight_command(
    arbitration: ArbitrationController,
    menu: MenuStateMachine,
    caplog,
):
    serial = BlockingSerialManager()
    set_app_state(
        serial_manager=serial,
        image_processor=FakeImageProcessor(),
        menu_state=menu,
        arbitration=arbitration,
    )
    app = create_app()
    client = TestClient(app)
    caplog.set_level("INFO")

    result: dict[str, object] = {}

    def _invoke_move():
        response = client.post(
            "/api/movement/move",
            json={"left_speed": 100, "right_speed": 100, "duration_ms": 10},
        )
        result["status_code"] = response.status_code

    worker = threading.Thread(target=_invoke_move)
    worker.start()

    assert serial.entered.wait(timeout=1.0)
    assert arbitration.is_local_allowed() is False

    blocked = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.95)
    assert blocked is False
    assert menu.selected_index == 0

    serial.release.set()
    worker.join(timeout=1.0)

    assert result["status_code"] == 200
    # Endpoint release should put arbitration into cooldown first.
    assert arbitration.snapshot()["state"] in ("cooldown", "local")
    assert any(
        "api.preempt_remote endpoint=/api/movement/move reason=movement" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        "api.release_remote endpoint=/api/movement/move" in record.getMessage()
        for record in caplog.records
    )


def test_menu_gestures_blocked_during_remote_state(
    menu: MenuStateMachine,
    arbitration: ArbitrationController,
    caplog,
):
    caplog.set_level("DEBUG")
    arbitration.preempt_local("movement")

    handled = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.95)
    assert handled is False
    assert menu.selected_index == 0
    assert any(
        "menu.gesture_blocked state=remote" in record.getMessage()
        for record in caplog.records
    )


def test_menu_gestures_blocked_during_cooldown(menu: MenuStateMachine, arbitration: ArbitrationController):
    arbitration.preempt_local("movement")

    async def _run():
        arbitration.release_remote()
        handled = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.95)
        assert handled is False
        assert menu.selected_index == 0

        await asyncio.sleep(0.01)
        assert arbitration.snapshot()["state"] == "cooldown"

    asyncio.run(_run())


def test_menu_gestures_allowed_after_cooldown_expires(menu: MenuStateMachine, arbitration: ArbitrationController):
    arbitration.preempt_local("movement")

    async def _run():
        arbitration.release_remote()
        await asyncio.sleep(0.07)

        handled = menu.handle_gesture(Gesture.THUMBS_UP, confidence=0.95)
        assert handled is True
        assert menu.selected_index == 1
        assert arbitration.is_local_allowed() is True

    asyncio.run(_run())


def test_arbitration_status_endpoint_returns_snapshot(app_client):
    client, _ = app_client

    response = client.get("/api/arbitration/status")
    assert response.status_code == 200

    payload = response.json()
    assert payload["state"] == "local"
    assert payload["reason"]
    assert "timestamp" in payload
    assert "cooldown_remaining_seconds" in payload


def test_force_local_endpoint_bypasses_cooldown(app_client, arbitration: ArbitrationController):
    client, _ = app_client

    arbitration.preempt_local("display_update")

    async def _enter_cooldown():
        arbitration.release_remote()
        await asyncio.sleep(0.001)

    asyncio.run(_enter_cooldown())
    assert arbitration.snapshot()["state"] == "cooldown"

    response = client.post("/api/arbitration/force-local")
    assert response.status_code == 200
    assert response.json()["status"] == "local"

    assert arbitration.snapshot()["state"] == "local"
    assert arbitration.is_local_allowed() is True


def test_remote_command_failure_still_releases_to_cooldown(
    arbitration: ArbitrationController,
    menu: MenuStateMachine,
):
    serial = FakeSerialManager(should_raise=True)
    set_app_state(
        serial_manager=serial,
        image_processor=FakeImageProcessor(),
        menu_state=menu,
        arbitration=arbitration,
    )
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/movement/move",
        json={"left_speed": 30, "right_speed": 30, "duration_ms": 50},
    )
    assert response.status_code == 500

    # Even on error, endpoint should have released REMOTE and entered cooldown.
    assert arbitration.snapshot()["state"] in ("cooldown", "local")


def test_preempt_near_cooldown_expiry_restarts_timer(menu: MenuStateMachine, arbitration: ArbitrationController):
    arbitration.preempt_local("movement")

    async def _run():
        arbitration.release_remote()

        await asyncio.sleep(0.049)
        arbitration.preempt_local("movement")
        arbitration.release_remote()

        # Immediately after restart we should still be in cooldown.
        assert arbitration.snapshot()["state"] == "cooldown"

        await asyncio.sleep(0.06)
        assert arbitration.snapshot()["state"] == "local"

    asyncio.run(_run())


def test_preempt_displays_remote_notice_via_serial(ws_manager: FakeWebSocketManager):
    serial = FakeSerialManager()
    arbitration = ArbitrationController(
        cooldown_seconds=0.05,
        serial_manager=serial,
        image_processor=FakeImageProcessor(),
        ws_manager=ws_manager,
    )

    async def _run():
        changed = arbitration.preempt_local("movement")
        assert changed is True
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert serial.commands
    assert serial.commands[0]["kind"] == "display_image"
    assert serial.commands[0]["payload"].startswith(b"notice:Remote Control Active")


def test_arbitration_state_transitions_broadcast_to_websocket(ws_manager: FakeWebSocketManager):
    arbitration = ArbitrationController(
        cooldown_seconds=0.02,
        serial_manager=FakeSerialManager(),
        image_processor=FakeImageProcessor(),
        ws_manager=ws_manager,
    )

    async def _run():
        arbitration.preempt_local("movement")
        await asyncio.sleep(0)
        arbitration.release_remote()
        await asyncio.sleep(0.03)

    asyncio.run(_run())

    states = [evt["state"] for evt in ws_manager.events]
    assert states == ["remote", "cooldown", "local"]
    reasons = [evt["reason"] for evt in ws_manager.events]
    assert reasons[0] == "movement"
    assert reasons[1] == "remote_released"
    assert reasons[2] == "cooldown_expired"


def test_websocket_broadcast_failure_does_not_block_state_transition(caplog):
    caplog.set_level("DEBUG")
    failing_ws = FakeWebSocketManager(should_raise=True)
    arbitration = ArbitrationController(
        cooldown_seconds=0.05,
        serial_manager=FakeSerialManager(),
        image_processor=FakeImageProcessor(),
        ws_manager=failing_ws,
    )

    async def _run():
        changed = arbitration.preempt_local("movement")
        assert changed is True
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert arbitration.snapshot()["state"] == "remote"
    assert any("arbitration.broadcast_failed state=remote" in record.getMessage() for record in caplog.records)
