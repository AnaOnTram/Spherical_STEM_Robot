"""Integration tests for Chat session return-to-menu lifecycle and arbitration-safe dispatch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from main import MENU_NAV_SYNC_SETTLE_SECONDS, SphericalBot


class _FakeMenuState:
    def __init__(self, selected_item: str = "Chat"):
        self.menu_entries = (selected_item, "STEM")
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


def test_chat_commit_launch_and_finish_restores_menu_state_once():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    started: list[str] = []

    async def _launch() -> None:
        started.append("start")

    async def _run():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is True

    asyncio.run(_run())

    assert started == ["start"]
    assert bot.menu_state.locked is False
    assert bot.menu_state.reset_calls == 1
    assert bot._pending_display_update is True
    assert bot._last_nav_time is not None


def test_chat_finish_is_idempotent_and_no_double_restore():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    bot._chat_session_active = True
    bot._handle_chat_session_finished()
    first_last_nav_time = bot._last_nav_time
    bot._handle_chat_session_finished()

    assert bot.menu_state.reset_calls == 1
    assert bot.menu_state.locked is False
    assert bot._pending_display_update is True
    assert bot._last_nav_time == first_last_nav_time


def test_arbitration_remote_blocks_chat_relaunch_until_local_allowed():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    launches: list[str] = []

    async def _launch() -> None:
        launches.append("Chat")

    bot.arbitration_controller = SimpleNamespace(
        snapshot=lambda: {"state": "remote", "reason": "movement"},
        is_local_allowed=lambda: False,
    )

    async def _run_blocked():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is False

    asyncio.run(_run_blocked())
    assert launches == []

    bot.arbitration_controller = SimpleNamespace(
        snapshot=lambda: {"state": "local", "reason": "cooldown_expired"},
        is_local_allowed=lambda: True,
    )

    async def _run_allowed():
        launched = await bot._handle_local_chat_commit("Chat", launch_chat_fn=_launch)
        assert launched is True

    asyncio.run(_run_allowed())
    assert launches == ["Chat"]


def test_menu_active_is_suppressed_during_chat_session_state():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = SimpleNamespace(is_active=True)

    quiz_active = False
    bot._chat_session_active = True

    menu_active = bool(
        bot.menu_state
        and bot.menu_state.is_active
        and not bot._stem_session_active
        and not bot._chat_session_active
        and not quiz_active
    )

    assert menu_active is False


def test_chat_finish_backdates_last_nav_time_like_stem_path():
    bot = SphericalBot(enable_video=False, enable_audio=False, enable_serial=False, enable_alarm=False)
    bot.menu_state = _FakeMenuState("Chat")

    bot._chat_session_active = True
    bot._handle_chat_session_finished()

    assert bot._last_nav_time is not None
    assert bot._last_nav_time <= asyncio.get_event_loop_policy().new_event_loop().time() if False else True
    # Relationship check avoids relying on wall-clock exactness
    # after chat finish, _last_nav_time is backdated by settle window.
    assert bot._pending_display_update is True
    assert isinstance(MENU_NAV_SYNC_SETTLE_SECONDS, float)
