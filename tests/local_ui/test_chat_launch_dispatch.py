"""Focused tests for deterministic local Chat launch dispatch contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from main import SphericalBot


class _FakeMenuState:
    def __init__(self, selected_item: str):
        self.menu_entries = (selected_item,)
        self.selected_index = 0
        self._commit_requested = True

    def consume_commit_requested(self) -> bool:
        if not self._commit_requested:
            return False
        self._commit_requested = False
        return True


def test_single_commit_dispatches_single_chat_launch_call():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    launch_calls: list[str] = []

    async def _launch() -> None:
        launch_calls.append("Chat")

    async def _run():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is True

    asyncio.run(_run())

    assert launch_calls == ["Chat"]


def test_repeated_loop_frames_do_not_duplicate_single_commit_launch():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")
    launch_calls: list[str] = []

    async def _launch() -> None:
        launch_calls.append("Chat")

    async def _simulate_loop_frames(frame_count: int) -> None:
        for _ in range(frame_count):
            if bot.menu_state.consume_commit_requested():
                selected_item = bot.menu_state.menu_entries[bot.menu_state.selected_index]
                await bot._handle_local_chat_commit(selected_item, launch_chat_fn=_launch)

    asyncio.run(_simulate_loop_frames(5))

    assert launch_calls == ["Chat"]


def test_arbitration_denied_suppresses_chat_launch():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.arbitration_controller = SimpleNamespace(
        snapshot=lambda: {"state": "remote", "reason": "movement"},
        is_local_allowed=lambda: False,
    )
    launch_calls: list[str] = []

    async def _launch() -> None:
        launch_calls.append("Chat")

    async def _run():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is False

    asyncio.run(_run())

    assert launch_calls == []
