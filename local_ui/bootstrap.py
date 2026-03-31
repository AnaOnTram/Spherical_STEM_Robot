"""Bootstrap readiness contract and startup orchestration for local UI."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Awaitable, Callable, Mapping, Sequence

from config import EINK_IMAGE_SIZE

logger = logging.getLogger(__name__)

REQUIRED_READINESS_KEYS: tuple[str, ...] = ("serial_manager", "image_processor")
BASELINE_HOME_MENU_ENTRIES: tuple[str, str, str, str] = (
    "STEM",
    "Chat",
    "Follow",
    "Call Parent",
)


class BootstrapPhase(str, Enum):
    """Startup phases for local UI home-menu bootstrap."""

    INITIALIZING = "initializing"
    HEALTH_CHECK = "health_check"
    RENDERING_HOME_MENU = "rendering_home_menu"
    PUBLISHING_HOME_MENU = "publishing_home_menu"
    HOME_MENU_READY = "home_menu_ready"
    ERROR = "error"


@dataclass
class BootstrapState:
    """Thread-safe in-memory bootstrap state.

    Fields intentionally contain metadata only (phase/timestamps/error summary)
    to keep diagnostics safe for API exposure.
    """

    phase: BootstrapPhase = BootstrapPhase.INITIALIZING
    home_menu_ready: bool = False
    last_error: str | None = None
    started_at: str = field(default_factory=lambda: _utc_now_iso())
    updated_at: str = field(default_factory=lambda: _utc_now_iso())
    last_error_at: str | None = None
    transition_timestamps: dict[str, str] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.transition_timestamps:
            self.transition_timestamps[self.phase.value] = self.started_at

    def transition(self, phase: BootstrapPhase | str) -> "BootstrapState":
        """Transition to a phase with idempotent same-phase behavior.

        Invalid phase values are mapped to terminal error state.
        """
        with self._lock:
            resolved_phase = _coerce_phase(phase)
            if resolved_phase is None:
                return self.record_error(f"invalid bootstrap phase: {phase!r}")

            if self.phase == BootstrapPhase.ERROR:
                # Terminal state: preserve first meaningful failure context.
                return self

            if resolved_phase == self.phase:
                return self

            now = _utc_now_iso()
            previous = self.phase.value
            self.phase = resolved_phase
            self.updated_at = now
            self.transition_timestamps.setdefault(resolved_phase.value, now)
            if resolved_phase == BootstrapPhase.HOME_MENU_READY:
                self.home_menu_ready = True

            logger.info(
                "bootstrap.phase_transition previous=%s current=%s ready=%s",
                previous,
                resolved_phase.value,
                self.home_menu_ready,
            )
            return self

    def record_error(self, error: str) -> "BootstrapState":
        """Enter terminal error state and preserve the first error context."""
        with self._lock:
            if self.phase == BootstrapPhase.ERROR and self.last_error:
                return self

            now = _utc_now_iso()
            self.phase = BootstrapPhase.ERROR
            self.home_menu_ready = False
            self.last_error = _sanitize_error(error)
            self.last_error_at = now
            self.updated_at = now
            self.transition_timestamps.setdefault(BootstrapPhase.ERROR.value, now)

            logger.error("bootstrap.error %s", self.last_error)
            return self

    def snapshot(self) -> dict[str, Any]:
        """Return an immutable metadata snapshot for API/status surfaces."""
        with self._lock:
            return {
                "phase": self.phase.value,
                "home_menu_ready": self.home_menu_ready,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "last_error": self.last_error,
                "last_error_at": self.last_error_at,
                "transition_timestamps": dict(self.transition_timestamps),
            }


def evaluate_readiness(
    components: Mapping[str, Any] | None,
    required_keys: tuple[str, ...] = REQUIRED_READINESS_KEYS,
) -> tuple[bool, str | None]:
    """Evaluate whether required runtime components are ready.

    Returns (is_ready, error_summary). The error summary is metadata-safe and
    intended for status surfaces.
    """
    if components is None:
        return False, "malformed component map: expected mapping, got None"

    if not isinstance(components, Mapping):
        return (
            False,
            f"malformed component map: expected mapping, got {type(components).__name__}",
        )

    missing_keys = [key for key in required_keys if key not in components]
    if missing_keys:
        return False, f"missing readiness keys: {', '.join(sorted(missing_keys))}"

    unavailable = [key for key in required_keys if components.get(key) is None]
    if unavailable:
        return False, f"required components unavailable: {', '.join(sorted(unavailable))}"

    return True, None


def normalize_home_menu_entries(
    entries: Sequence[str] | None,
    fallback: tuple[str, str, str, str] = BASELINE_HOME_MENU_ENTRIES,
) -> tuple[str, str, str, str]:
    """Normalize menu entries to the deterministic four-item baseline.

    If entries are missing or malformed, the baseline is used.
    """
    if entries is None:
        return fallback

    cleaned = tuple(str(item).strip() for item in entries if str(item).strip())
    if len(cleaned) != 4:
        return fallback

    return cleaned  # type: ignore[return-value]


async def run_bootstrap_flow(
    *,
    state: BootstrapState,
    components: Mapping[str, Any],
    render_home_menu: Callable[[Sequence[str]], bytes],
    publish_image: Callable[[bytes], Awaitable[Any]],
    required_keys: tuple[str, ...] = REQUIRED_READINESS_KEYS,
    readiness_timeout_s: float,
    render_timeout_s: float,
    publish_timeout_s: float,
    menu_entries: Sequence[str] | None = None,
) -> BootstrapState:
    """Execute readiness → render → publish bootstrap flow exactly once."""
    if state.phase == BootstrapPhase.HOME_MENU_READY:
        logger.info("bootstrap.skip already-ready")
        return state

    state.transition(BootstrapPhase.HEALTH_CHECK)
    try:
        is_ready, readiness_error = await asyncio.wait_for(
            asyncio.to_thread(evaluate_readiness, components, required_keys),
            timeout=readiness_timeout_s,
        )
    except asyncio.TimeoutError:
        return state.record_error("readiness_timeout: health-check exceeded bootstrap budget")

    if not is_ready:
        return state.record_error(f"readiness_error: {readiness_error}")

    state.transition(BootstrapPhase.RENDERING_HOME_MENU)
    normalized_entries = normalize_home_menu_entries(menu_entries)

    try:
        image_payload = await asyncio.wait_for(
            asyncio.to_thread(render_home_menu, normalized_entries),
            timeout=render_timeout_s,
        )
    except asyncio.TimeoutError:
        return state.record_error("render_timeout: home-menu render exceeded bootstrap budget")
    except Exception as exc:
        return state.record_error(f"render_error: {exc}")

    if not isinstance(image_payload, (bytes, bytearray)):
        return state.record_error(
            "render_malformed: renderer returned non-bytes payload"
        )

    payload_bytes = bytes(image_payload)
    if len(payload_bytes) != EINK_IMAGE_SIZE:
        return state.record_error(
            f"render_malformed: payload_size={len(payload_bytes)} expected={EINK_IMAGE_SIZE}"
        )

    state.transition(BootstrapPhase.PUBLISHING_HOME_MENU)
    try:
        serial_response = await asyncio.wait_for(
            publish_image(payload_bytes),
            timeout=publish_timeout_s,
        )
    except asyncio.TimeoutError:
        return state.record_error("publish_timeout: serial publish exceeded bootstrap budget")
    except Exception as exc:
        return state.record_error(f"publish_error: {exc}")

    status_value = _response_status_value(serial_response)
    if status_value != "OK":
        response_message = _sanitize_error(getattr(serial_response, "message", ""))
        return state.record_error(
            f"publish_error: status={status_value or 'UNKNOWN'} message={response_message or 'n/a'}"
        )

    state.transition(BootstrapPhase.HOME_MENU_READY)
    logger.info("bootstrap.complete home-menu published successfully")
    return state


def transition_phase(state: BootstrapState, phase: BootstrapPhase | str) -> BootstrapState:
    """Module-level helper used by startup wiring for explicit transitions."""
    return state.transition(phase)


def record_bootstrap_error(state: BootstrapState, error: str) -> BootstrapState:
    """Module-level helper used by startup wiring to set terminal errors."""
    return state.record_error(error)


def _coerce_phase(phase: BootstrapPhase | str) -> BootstrapPhase | None:
    if isinstance(phase, BootstrapPhase):
        return phase
    if isinstance(phase, str):
        try:
            return BootstrapPhase(phase)
        except ValueError:
            return None
    return None


def _response_status_value(serial_response: Any) -> str | None:
    """Extract an uppercase status token from a serial response-like object."""
    status = getattr(serial_response, "status", None)
    if status is None:
        return None

    if isinstance(status, str):
        return status.upper()

    value = getattr(status, "value", None)
    if isinstance(value, str):
        return value.upper()

    return str(status).upper() if status else None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_error(error: str) -> str:
    return " ".join(str(error).split())[:240]
