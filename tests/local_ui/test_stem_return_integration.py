"""Integration tests for STEM session return-to-menu lifecycle and arbitration-safe exit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from main import SphericalBot


class _FakeMenuState:
    def __init__(self, selected_item: str = "STEM"):
        self.menu_entries = (selected_item, "Chat")
        self.selected_index = 0
        self._commit_requested = True
        self._navigation_requested = False
        self.locked = True
        self.reset_calls = 0

    def consume_commit_requested(self) -> bool:
        if not self._commit_requested:
            return False
        self._commit_requested = False
        return True

    def consume_navigation_requested(self) -> bool:
        if not self._navigation_requested:
            return False
        self._navigation_requested = False
        return True

    def reset_after_external_session(self) -> None:
        self.reset_calls += 1
        self.locked = False


def test_stem_commit_launch_and_finish_restores_menu_state_once():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("STEM")

    started: list[str] = []

    async def _launch(_item: str) -> None:
        bot._stem_session_active = True
        started.append("start")

    async def _run():
        launched = await bot._handle_local_stem_commit("STEM", launch_fn=_launch)
        assert launched is True
        bot._handle_stem_session_finished()

    asyncio.run(_run())

    assert started == ["start"]
    assert bot.menu_state.locked is False
    assert bot.menu_state.reset_calls == 1


def test_stem_finish_is_idempotent_and_no_double_restore():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("STEM")

    bot._stem_session_active = True
    bot._handle_stem_session_finished()
    bot._handle_stem_session_finished()

    assert bot.menu_state.reset_calls == 1
    assert bot.menu_state.locked is False


def test_arbitration_remote_blocks_relaunch_after_finish_until_local_allowed(monkeypatch):
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("STEM")

    launches: list[str] = []

    async def _launch(item: str) -> None:
        launches.append(item)

    arbitration = SimpleNamespace(
        snapshot=lambda: {"state": "remote", "reason": "movement"},
        is_local_allowed=lambda: False,
    )
    bot.arbitration_controller = arbitration

    async def _run_blocked():
        launched = await bot._handle_local_stem_commit("STEM", launch_fn=_launch)
        assert launched is False

    asyncio.run(_run_blocked())
    assert launches == []

    bot.arbitration_controller = SimpleNamespace(
        snapshot=lambda: {"state": "local", "reason": "cooldown_expired"},
        is_local_allowed=lambda: True,
    )

    async def _run_allowed():
        launched = await bot._handle_local_stem_commit("STEM", launch_fn=_launch)
        assert launched is True

    asyncio.run(_run_allowed())
    assert launches == ["STEM"]
