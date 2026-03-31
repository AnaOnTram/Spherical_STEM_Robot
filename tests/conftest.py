"""Shared test fixtures for local bootstrap/state tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_ui.bootstrap import BootstrapState


@pytest.fixture
def bootstrap_state() -> BootstrapState:
    """Fresh bootstrap state per test."""
    return BootstrapState()


@pytest.fixture
def ready_components() -> dict[str, object]:
    """Deterministic component map that satisfies readiness."""
    return {
        "serial_manager": object(),
        "image_processor": object(),
        "video_encoder": object(),
    }


@pytest.fixture
def components_missing_required_key() -> dict[str, object]:
    """Component map with one required readiness key missing."""
    return {
        "serial_manager": object(),
    }


@pytest.fixture
def components_with_null_required() -> dict[str, object | None]:
    """Component map where a required dependency is present but unavailable."""
    return {
        "serial_manager": None,
        "image_processor": object(),
    }
