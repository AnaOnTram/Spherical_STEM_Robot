"""Bootstrap readiness contract for local UI startup.

This module defines deterministic, metadata-only startup state that can be
shared between startup orchestration and status APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Mapping

REQUIRED_READINESS_KEYS: tuple[str, ...] = ("serial_manager", "image_processor")


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
            self.phase = resolved_phase
            self.updated_at = now
            self.transition_timestamps.setdefault(resolved_phase.value, now)
            if resolved_phase == BootstrapPhase.HOME_MENU_READY:
                self.home_menu_ready = True
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_error(error: str) -> str:
    return " ".join(str(error).split())[:240]
