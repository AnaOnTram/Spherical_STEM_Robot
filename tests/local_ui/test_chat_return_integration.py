"""Integration tests for Chat session return-to-menu lifecycle and arbitration-safe dispatch."""

from __future__ import annotations

import asyncio

from main import SphericalBot


class _FakeMenuState:
    def __init__(self, selected_item: str = "Chat"):
        self.menu_entries = ("STEM", selected_item)
        self.selected_index = 1
        self.locked = True
        self.reset_calls = 0

    def reset_after_external_session(self) -> None:
        self.reset_calls += 1
        self.locked = False


def test_chat_commit_launch_and_finish_restores_menu_state_once():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    starts: list[str] = []

    async def _launch() -> None:
        starts.append("start")

    async def _run():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is True

    asyncio.run(_run())

    assert starts == ["start"]
    assert bot.menu_state.locked is False
    assert bot.menu_state.reset_calls == 1
    assert bot._chat_session_active is False


def test_chat_finish_is_idempotent_and_no_double_restore():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    bot._chat_session_active = True
    bot._handle_chat_session_finished()
    bot._handle_chat_session_finished()

    assert bot.menu_state.reset_calls == 1
    assert bot.menu_state.locked is False


def test_chat_finish_requests_menu_redraw_with_backdated_nav_time():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    bot._chat_session_active = True
    bot._pending_display_update = False

    bot._handle_chat_session_finished()

    assert bot._pending_display_update is True
    assert bot._last_nav_time is not None


def test_chat_session_failure_path_still_restores_menu_once():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    async def _failing_launch() -> None:
        raise RuntimeError("chat launch failure")

    async def _run() -> None:
        await bot._run_local_chat_session(launch_chat_fn=_failing_launch)

    asyncio.run(_run())

    assert bot.menu_state.reset_calls == 1
    assert bot.menu_state.locked is False
    assert bot._chat_session_active is False
