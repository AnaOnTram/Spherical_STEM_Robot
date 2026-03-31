"""Contract tests for local bootstrap readiness and phase semantics."""

from __future__ import annotations

from local_ui.bootstrap import (
    BootstrapPhase,
    evaluate_readiness,
    record_bootstrap_error,
    transition_phase,
)


def test_initial_state_exposes_required_observability_fields(bootstrap_state):
    snapshot = bootstrap_state.snapshot()

    assert snapshot["phase"] == BootstrapPhase.INITIALIZING.value
    assert snapshot["home_menu_ready"] is False
    assert snapshot["last_error"] is None
    assert snapshot["started_at"]
    assert snapshot["updated_at"]
    assert snapshot["transition_timestamps"]["initializing"] == snapshot["started_at"]


def test_readiness_passes_for_required_components(ready_components):
    is_ready, error = evaluate_readiness(ready_components)
    assert is_ready is True
    assert error is None


def test_readiness_fails_for_missing_required_keys(components_missing_required_key):
    is_ready, error = evaluate_readiness(components_missing_required_key)

    assert is_ready is False
    assert error is not None
    assert "missing readiness keys" in error
    assert "image_processor" in error


def test_readiness_fails_for_unavailable_required_component(components_with_null_required):
    is_ready, error = evaluate_readiness(components_with_null_required)

    assert is_ready is False
    assert error is not None
    assert "required components unavailable" in error
    assert "serial_manager" in error


def test_readiness_rejects_malformed_component_map():
    is_ready_none, error_none = evaluate_readiness(None)
    is_ready_bad, error_bad = evaluate_readiness(["serial_manager", "image_processor"])  # type: ignore[arg-type]

    assert is_ready_none is False
    assert "malformed component map" in (error_none or "")
    assert is_ready_bad is False
    assert "malformed component map" in (error_bad or "")


def test_phase_transition_same_phase_is_idempotent(bootstrap_state):
    first_snapshot = bootstrap_state.snapshot()

    transition_phase(bootstrap_state, BootstrapPhase.INITIALIZING)

    second_snapshot = bootstrap_state.snapshot()
    assert second_snapshot["phase"] == first_snapshot["phase"]
    assert second_snapshot["updated_at"] == first_snapshot["updated_at"]
    assert second_snapshot["transition_timestamps"] == first_snapshot["transition_timestamps"]


def test_invalid_phase_name_maps_to_terminal_error(bootstrap_state):
    transition_phase(bootstrap_state, "not-a-real-phase")
    snapshot = bootstrap_state.snapshot()

    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert snapshot["home_menu_ready"] is False
    assert "invalid bootstrap phase" in (snapshot["last_error"] or "")


def test_home_menu_ready_transition_sets_ready_flag(bootstrap_state):
    transition_phase(bootstrap_state, BootstrapPhase.HEALTH_CHECK)
    transition_phase(bootstrap_state, BootstrapPhase.RENDERING_HOME_MENU)
    transition_phase(bootstrap_state, BootstrapPhase.PUBLISHING_HOME_MENU)
    transition_phase(bootstrap_state, BootstrapPhase.HOME_MENU_READY)

    snapshot = bootstrap_state.snapshot()

    assert snapshot["phase"] == BootstrapPhase.HOME_MENU_READY.value
    assert snapshot["home_menu_ready"] is True
    assert "home_menu_ready" in snapshot["transition_timestamps"]


def test_terminal_error_preserves_first_failure_context(bootstrap_state):
    record_bootstrap_error(bootstrap_state, "serial manager missing")
    first_snapshot = bootstrap_state.snapshot()

    # Attempt overwrite with a different error must be ignored.
    record_bootstrap_error(bootstrap_state, "image processor missing")
    second_snapshot = bootstrap_state.snapshot()

    assert first_snapshot["phase"] == BootstrapPhase.ERROR.value
    assert second_snapshot["phase"] == BootstrapPhase.ERROR.value
    assert first_snapshot["last_error"] == "serial manager missing"
    assert second_snapshot["last_error"] == "serial manager missing"
    assert first_snapshot["last_error_at"] == second_snapshot["last_error_at"]


def test_error_state_never_claims_home_menu_ready(bootstrap_state, ready_components):
    is_ready, error = evaluate_readiness(ready_components)
    assert is_ready is True
    assert error is None

    record_bootstrap_error(bootstrap_state, "forced failure")
    transition_phase(bootstrap_state, BootstrapPhase.HOME_MENU_READY)

    snapshot = bootstrap_state.snapshot()
    assert snapshot["phase"] == BootstrapPhase.ERROR.value
    assert snapshot["home_menu_ready"] is False
