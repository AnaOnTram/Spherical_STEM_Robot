"""API tests for the local UI bootstrap status endpoint."""

from fastapi.testclient import TestClient
from api.routes import create_app, set_app_state
from local_ui.bootstrap import BootstrapState, BootstrapPhase

def test_local_ui_status_ready_state():
    """Test API correctly reflects a ready bootstrap state."""
    app = create_app()
    state = BootstrapState()
    state.transition(BootstrapPhase.HEALTH_CHECK)
    state.transition(BootstrapPhase.RENDERING_HOME_MENU)
    state.transition(BootstrapPhase.PUBLISHING_HOME_MENU)
    state.transition(BootstrapPhase.HOME_MENU_READY)
    
    set_app_state(bootstrap_state=state)
    
    client = TestClient(app)
    response = client.get("/api/local-ui/status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "home_menu_ready"
    assert data["home_menu_ready"] is True
    assert data["last_error"] is None
    assert "transition_timestamps" in data

def test_local_ui_status_error_state():
    """Test API correctly reflects an error bootstrap state."""
    app = create_app()
    state = BootstrapState()
    state.record_error("readiness_error: serial manager missing")
    
    set_app_state(bootstrap_state=state)
    
    client = TestClient(app)
    response = client.get("/api/local-ui/status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "error"
    assert data["home_menu_ready"] is False
    assert data["last_error"] == "readiness_error: serial manager missing"
    assert "last_error_at" in data

def test_local_ui_status_missing_state():
    """Test API behavior when bootstrap state is not present in app state."""
    app = create_app()
    set_app_state(bootstrap_state=None)
    
    client = TestClient(app)
    response = client.get("/api/local-ui/status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "error"
    assert data["home_menu_ready"] is False
    assert "bootstrap_state not found" in data["last_error"]
