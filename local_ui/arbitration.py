"""Remote/local arbitration state machine with cooldown resume policy."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from config import REMOTE_PREEMPT_COOLDOWN_SECONDS

logger = logging.getLogger(__name__)


class ArbitrationState(Enum):
    """Control authority states between local gestures and remote commands."""

    LOCAL = "local"
    REMOTE = "remote"
    COOLDOWN = "cooldown"


class ArbitrationController:
    """Owns local/remote control arbitration with an async cooldown timer."""

    def __init__(self, cooldown_seconds: float = REMOTE_PREEMPT_COOLDOWN_SECONDS):
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")

        self._cooldown_seconds = float(cooldown_seconds)
        self._state = ArbitrationState.LOCAL
        self._reason = "initial_local"
        self._timestamp = self._iso_timestamp()

        self._cooldown_task: asyncio.Task[None] | None = None
        self._cooldown_started_monotonic: float | None = None

    @property
    def state(self) -> ArbitrationState:
        return self._state

    def preempt_local(self, reason: str) -> bool:
        """Transition to REMOTE. Returns True iff state changed."""
        if not reason or not reason.strip():
            raise ValueError("reason must be a non-empty string")

        reason = reason.strip()

        if self._state == ArbitrationState.REMOTE:
            logger.info(
                "arbitration.preempt_ignored state=%s reason=%s",
                self._state.value,
                reason,
            )
            return False

        self._cancel_cooldown_task("preempt_local")
        self._transition_to(ArbitrationState.REMOTE, reason)
        return True

    def release_remote(self) -> bool:
        """Transition REMOTE->COOLDOWN and start async timer."""
        if self._state != ArbitrationState.REMOTE:
            logger.warning(
                "arbitration.release_ignored state=%s reason=not_remote",
                self._state.value,
            )
            return False

        self._transition_to(ArbitrationState.COOLDOWN, "remote_released")
        self._start_cooldown_timer()
        return True

    def force_local(self) -> bool:
        """Immediately return to LOCAL and bypass cooldown."""
        if self._state == ArbitrationState.LOCAL:
            logger.info("arbitration.force_local_noop state=local")
            return False

        self._cancel_cooldown_task("force_local")
        self._transition_to(ArbitrationState.LOCAL, "force_local")
        return True

    def is_local_allowed(self) -> bool:
        """True only when LOCAL control is active."""
        return self._state == ArbitrationState.LOCAL

    def snapshot(self) -> dict[str, Any]:
        """Return an API-ready arbitration status snapshot."""
        remaining = 0.0
        if self._state == ArbitrationState.COOLDOWN and self._cooldown_started_monotonic is not None:
            elapsed = time.monotonic() - self._cooldown_started_monotonic
            remaining = max(0.0, self._cooldown_seconds - elapsed)

        return {
            "state": self._state.value,
            "reason": self._reason,
            "timestamp": self._timestamp,
            "cooldown_remaining_seconds": round(remaining, 3),
        }

    def _start_cooldown_timer(self) -> None:
        self._cancel_cooldown_task("restart_cooldown")
        self._cooldown_started_monotonic = time.monotonic()

        try:
            self._cooldown_task = asyncio.create_task(self._run_cooldown_timer())
        except RuntimeError as exc:
            raise RuntimeError(
                "ArbitrationController.release_remote() requires a running asyncio event loop"
            ) from exc

    async def _run_cooldown_timer(self) -> None:
        try:
            await asyncio.sleep(self._cooldown_seconds)
            if self._state == ArbitrationState.COOLDOWN:
                self._transition_to(ArbitrationState.LOCAL, "cooldown_expired")
                self._cooldown_started_monotonic = None
        except asyncio.CancelledError:
            raise
        finally:
            if self._cooldown_task is asyncio.current_task():
                self._cooldown_task = None

    def _cancel_cooldown_task(self, reason: str) -> None:
        task = self._cooldown_task
        if task is None or task.done():
            self._cooldown_task = None
            if reason == "force_local":
                self._cooldown_started_monotonic = None
            return

        task.cancel()
        if reason == "force_local":
            logger.info("arbitration.timer_cancelled state=cooldown reason=force_local")
            self._cooldown_started_monotonic = None

    def _transition_to(self, new_state: ArbitrationState, reason: str) -> None:
        if self._state == new_state:
            return

        old_state = self._state
        timestamp = self._iso_timestamp()

        self._state = new_state
        self._reason = reason
        self._timestamp = timestamp

        logger.info(
            "arbitration.state_transition from=%s to=%s reason=%s timestamp=%s",
            old_state.value,
            new_state.value,
            reason,
            timestamp,
        )

    @staticmethod
    def _iso_timestamp() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
