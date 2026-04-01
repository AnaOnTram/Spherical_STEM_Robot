"""Tests for ArbitrationController state transitions and cooldown timer behavior."""

import asyncio
import logging

import pytest

from local_ui.arbitration import ArbitrationController, ArbitrationState


class TestArbitrationConstruction:
    def test_initial_state_is_local(self):
        controller = ArbitrationController(cooldown_seconds=1.0)
        snapshot = controller.snapshot()

        assert controller.state == ArbitrationState.LOCAL
        assert controller.is_local_allowed() is True
        assert snapshot["state"] == "local"
        assert snapshot["cooldown_remaining_seconds"] == 0.0

    def test_rejects_negative_cooldown(self):
        with pytest.raises(ValueError, match=">= 0"):
            ArbitrationController(cooldown_seconds=-0.1)


class TestArbitrationTransitions:
    def test_preempt_local_transitions_local_to_remote(self):
        controller = ArbitrationController(cooldown_seconds=1.0)

        changed = controller.preempt_local("remote_api")

        assert changed is True
        assert controller.state == ArbitrationState.REMOTE
        assert controller.is_local_allowed() is False

    def test_preempt_local_rejects_empty_reason(self):
        controller = ArbitrationController(cooldown_seconds=1.0)

        with pytest.raises(ValueError, match="non-empty"):
            controller.preempt_local("   ")

    def test_double_preemption_is_idempotent(self):
        controller = ArbitrationController(cooldown_seconds=1.0)

        first = controller.preempt_local("first")
        second = controller.preempt_local("second")

        assert first is True
        assert second is False
        assert controller.state == ArbitrationState.REMOTE
        # Idempotent call should not overwrite current reason/state snapshot
        assert controller.snapshot()["reason"] == "first"

    def test_release_remote_from_local_is_noop(self, caplog):
        controller = ArbitrationController(cooldown_seconds=1.0)

        changed = controller.release_remote()

        assert changed is False
        assert controller.state == ArbitrationState.LOCAL
        assert "arbitration.release_ignored" in caplog.text

    def test_force_local_multiple_times_is_safe(self):
        controller = ArbitrationController(cooldown_seconds=1.0)

        first = controller.force_local()
        second = controller.force_local()

        assert first is False
        assert second is False
        assert controller.state == ArbitrationState.LOCAL


class TestArbitrationCooldown:
    def test_release_remote_enters_cooldown_and_timer_expires_to_local(self):
        async def scenario():
            controller = ArbitrationController(cooldown_seconds=0.05)
            controller.preempt_local("remote_api")

            changed = controller.release_remote()
            assert changed is True
            assert controller.state == ArbitrationState.COOLDOWN
            assert controller.is_local_allowed() is False

            await asyncio.sleep(0.08)
            assert controller.state == ArbitrationState.LOCAL
            assert controller.is_local_allowed() is True
            assert controller.snapshot()["reason"] == "cooldown_expired"

        asyncio.run(scenario())

    def test_cooldown_zero_transitions_immediately(self):
        async def scenario():
            controller = ArbitrationController(cooldown_seconds=0.0)
            controller.preempt_local("remote_api")
            changed = controller.release_remote()

            assert changed is True
            assert controller.state == ArbitrationState.COOLDOWN

            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert controller.state == ArbitrationState.LOCAL

        asyncio.run(scenario())

    def test_force_local_during_cooldown_cancels_timer(self, caplog):
        caplog.set_level(logging.INFO, logger="local_ui.arbitration")

        async def scenario():
            controller = ArbitrationController(cooldown_seconds=0.2)
            controller.preempt_local("remote_api")
            controller.release_remote()
            assert controller.state == ArbitrationState.COOLDOWN

            changed = controller.force_local()
            assert changed is True
            assert controller.state == ArbitrationState.LOCAL
            assert controller.is_local_allowed() is True

            await asyncio.sleep(0.25)
            assert controller.state == ArbitrationState.LOCAL

        asyncio.run(scenario())
        assert "arbitration.timer_cancelled state=cooldown reason=force_local" in caplog.text


class TestArbitrationSnapshotAndLogs:
    def test_snapshot_returns_expected_shape_and_values(self):
        async def scenario():
            controller = ArbitrationController(cooldown_seconds=0.2)
            controller.preempt_local("move_command")
            controller.release_remote()

            snapshot = controller.snapshot()
            assert snapshot["state"] == "cooldown"
            assert snapshot["reason"] == "remote_released"
            assert isinstance(snapshot["timestamp"], str)
            assert 0.0 <= snapshot["cooldown_remaining_seconds"] <= 0.2

            controller.force_local()  # cleanup timer task

        asyncio.run(scenario())

    def test_state_transition_logs_include_from_to_reason_timestamp(self, caplog):
        caplog.set_level(logging.INFO, logger="local_ui.arbitration")

        async def scenario():
            controller = ArbitrationController(cooldown_seconds=0.01)
            controller.preempt_local("api_test")
            controller.release_remote()
            await asyncio.sleep(0.03)

        asyncio.run(scenario())

        assert "arbitration.state_transition" in caplog.text
        assert "from=local to=remote reason=api_test" in caplog.text
        assert "from=remote to=cooldown reason=remote_released" in caplog.text
        assert "from=cooldown to=local reason=cooldown_expired" in caplog.text
        assert "timestamp=" in caplog.text
