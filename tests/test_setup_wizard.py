"""Unit and integration tests for the first-run onboarding setup wizard."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app


@pytest.fixture
def unconfigured_app(tmp_path: Path):
    """Provide an app instance pointing to a non-existent config path."""
    non_existent_config = tmp_path / "non_existent_config.yaml"
    app = create_app(
        config=None,
        repository=InMemoryEventRepository(),
        config_path=non_existent_config,
    )
    return app, non_existent_config


def test_setup_redirect_when_config_missing(unconfigured_app) -> None:
    """TR-10: Verify root request redirects with HTTP 307 to /setup when unconfigured."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/setup"


def test_setup_status_indicates_unconfigured(unconfigured_app) -> None:
    """TR-11: Verify GET /api/setup/status reports configured is False."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_setup_test_ai_connection_success(unconfigured_app) -> None:
    """TR-12: Verify POST /api/setup/test-ai validates provider connection."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-ai",
        json={"provider": "mock", "model": "mock-vision-v1"},
    )
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_setup_test_camera_stream_validation(unconfigured_app) -> None:
    """TR-13: Verify POST /api/setup/test-camera probes camera stream URL."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-camera",
        json={"stream_url": "rtsp://mock-cam/live"},
    )
    assert response.status_code == 200 and response.json()["reachable"] is True


def test_setup_test_notification_ping(unconfigured_app) -> None:
    """TR-14: Verify POST /api/setup/test-notification dispatches test ping."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-notification",
        json={"target_type": "webhook", "destination": "http://localhost:8080/test-webhook"},
    )
    assert response.status_code == 200 and response.json()["sent"] is True


def test_setup_finalize_generates_valid_yaml(unconfigured_app) -> None:
    """TR-15: Verify POST /api/setup/finalize writes valid YAML config and transitions state."""
    app, target_path = unconfigured_app
    client = TestClient(app)

    payload = {
        "system_name": "Onboarded Lookout Installation",
        "cameras": [
            {
                "id": "porch",
                "name": "Porch Camera",
                "stream_url": "rtsp://localhost:8554/porch",
                "camera_type": "generic",
            }
        ],
        "residents": [
            {
                "id": "alice",
                "name": "Alice Smith",
                "notification_targets": ["default-webhook"],
            }
        ],
        "notification_targets": [
            {
                "name": "default-webhook",
                "target_type": "webhook",
                "destination": "http://localhost:8080/alert",
            }
        ],
    }

    response = client.post("/api/setup/finalize", json=payload)
    assert response.status_code == 200
    assert target_path.exists() and "system_name" in target_path.read_text()

    # Verify that app is now configured
    status_res = client.get("/api/setup/status")
    assert status_res.status_code == 200
    assert status_res.json()["configured"] is True
