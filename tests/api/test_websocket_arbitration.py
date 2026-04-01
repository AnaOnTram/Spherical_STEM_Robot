"""Tests for arbitration-specific websocket event broadcasting."""

import asyncio

import pytest

from api.websocket import EventType, WebSocketEvent, WebSocketManager


def test_event_type_includes_arbitration_state_changed():
    assert EventType.ARBITRATION_STATE_CHANGED.value == "arbitration_state_changed"


def test_broadcast_arbitration_rejects_none_state():
    manager = WebSocketManager()

    async def _run():
        with pytest.raises(ValueError, match="state"):
            await manager.broadcast_arbitration(None, "movement")

    asyncio.run(_run())


def test_broadcast_arbitration_rejects_none_reason():
    manager = WebSocketManager()

    async def _run():
        with pytest.raises(ValueError, match="reason"):
            await manager.broadcast_arbitration("remote", None)

    asyncio.run(_run())


def test_websocket_event_serialization_for_arbitration_payload():
    event = WebSocketEvent(
        event_type=EventType.ARBITRATION_STATE_CHANGED,
        data={"state": "cooldown", "reason": "remote_released"},
    )

    payload = event.to_json()

    assert "arbitration_state_changed" in payload
    assert "cooldown" in payload
    assert "remote_released" in payload
