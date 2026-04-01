"""Regression tests for quiz session lifecycle callbacks."""

from __future__ import annotations

import asyncio

from api.routes import _quiz_state, quiz_stop, set_app_state


class _FakeEngine:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


def test_quiz_stop_triggers_on_finish_callback():
    callback_calls: list[str] = []

    def _on_finish() -> None:
        callback_calls.append("called")

    engine = _FakeEngine()
    _quiz_state["engine"] = engine
    set_app_state(on_quiz_finished=_on_finish)

    result = asyncio.run(quiz_stop())

    assert result["success"] is True
    assert engine.stop_calls == 1
    assert _quiz_state["engine"] is None
    assert callback_calls == ["called"]
