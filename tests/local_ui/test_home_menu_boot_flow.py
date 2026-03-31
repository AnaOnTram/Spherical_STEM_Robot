"""Runtime bootstrap flow tests for S01 home-menu publish sequencing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from config import EINK_IMAGE_SIZE
from cv_engine.image_processor import EInkImageProcessor
from local_ui.bootstrap import (
    BASELINE_HOME_MENU_ENTRIES,
    BootstrapPhase,
    BootstrapState,
    run_bootstrap_flow,
)


def _ready_components() -> dict[str, object]:
    return {
        "serial_manager": object(),
        "image_processor": object(),
    }


@dataclass
class FakeSerialResponse:
    status: str
    message: str = ""


def test_bootstrap_success_transitions_to_home_menu_ready_once():
    state = BootstrapState()
    render_calls: list[tuple[str, ...]] = []
    publish_calls: list[bytes] = []

    def render_home_menu(entries):
        render_calls.append(tuple(entries))
        return b"\x00" * EINK_IMAGE_SIZE

    async def publish_image(payload: bytes):
        publish_calls.append(payload)
        return FakeSerialResponse(status="OK", message="displayed")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.2,
        )
    )

    # Second call should be a no-op for publish/render once ready.
    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.2,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.HOME_MENU_READY.value
    assert snapshot["home_menu_ready"] is True
    assert render_calls == [BASELINE_HOME_MENU_ENTRIES]
    assert len(publish_calls) == 1


def test_bootstrap_fails_on_serial_non_ok_response():
    state = BootstrapState()

    def render_home_menu(_entries):
        return b"\x00" * EINK_IMAGE_SIZE

    async def publish_image(_payload: bytes):
        return FakeSerialResponse(status="ERR", message="serial disconnected")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.2,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert snapshot["home_menu_ready"] is False
    assert "publish_error" in (snapshot["last_error"] or "")
    assert "status=ERR" in (snapshot["last_error"] or "")


def test_bootstrap_fails_on_publish_timeout():
    state = BootstrapState()

    def render_home_menu(_entries):
        return b"\x00" * EINK_IMAGE_SIZE

    async def publish_image(_payload: bytes):
        await asyncio.sleep(0.05)
        return FakeSerialResponse(status="OK", message="late")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.01,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert "publish_timeout" in (snapshot["last_error"] or "")


def test_bootstrap_fails_on_malformed_render_payload_size():
    state = BootstrapState()
    publish_calls = 0

    def render_home_menu(_entries):
        return b"\x00" * 10

    async def publish_image(_payload: bytes):
        nonlocal publish_calls
        publish_calls += 1
        return FakeSerialResponse(status="OK", message="should not send")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.2,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert "render_malformed" in (snapshot["last_error"] or "")
    assert publish_calls == 0


def test_bootstrap_fails_readiness_when_required_component_missing():
    state = BootstrapState()
    render_calls = 0

    def render_home_menu(_entries):
        nonlocal render_calls
        render_calls += 1
        return b"\x00" * EINK_IMAGE_SIZE

    async def publish_image(_payload: bytes):
        return FakeSerialResponse(status="OK", message="not reached")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components={"serial_manager": object()},
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.2,
            publish_timeout_s=0.2,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert "readiness_error" in (snapshot["last_error"] or "")
    assert render_calls == 0


def test_bootstrap_fails_on_render_timeout():
    state = BootstrapState()

    def render_home_menu(_entries):
        time.sleep(0.05)
        return b"\x00" * EINK_IMAGE_SIZE

    async def publish_image(_payload: bytes):
        return FakeSerialResponse(status="OK", message="not reached")

    asyncio.run(
        run_bootstrap_flow(
            state=state,
            components=_ready_components(),
            render_home_menu=render_home_menu,
            publish_image=publish_image,
            readiness_timeout_s=0.2,
            render_timeout_s=0.01,
            publish_timeout_s=0.2,
        )
    )

    snapshot = state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert "render_timeout" in (snapshot["last_error"] or "")


def test_home_menu_renderer_is_deterministic_and_normalizes_bad_entry_count():
    processor = EInkImageProcessor(dither=False)

    baseline_a = processor.render_home_menu(BASELINE_HOME_MENU_ENTRIES)
    baseline_b = processor.render_home_menu(["STEM", "Chat", "Follow", "Call Parent"])
    normalized = processor.render_home_menu(["only one entry"])

    assert len(baseline_a) == EINK_IMAGE_SIZE
    assert baseline_a == baseline_b
    assert normalized == baseline_a
