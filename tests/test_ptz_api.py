"""Integration tests for PTZ REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig
from lookout.domain.ptz_service import PtzControlService


@pytest.fixture
def base_config(tmp_path) -> LookoutConfig:
    return LookoutConfig(
        system_name="PTZ API Test",
        storage=StorageConfig(base_path=str(tmp_path / "data")),
        cameras=[
            CameraConfig(
                id="cam1",
                name="Cam 1",
                stream_url="rtsp://localhost:8554/cam1",
            ),
        ],
    )


@pytest.fixture
def ptz_enabled_config(tmp_path) -> LookoutConfig:
    return LookoutConfig(
        system_name="PTZ API Test",
        storage=StorageConfig(base_path=str(tmp_path / "data")),
        cameras=[
            CameraConfig(
                id="cam1",
                name="Cam 1",
                stream_url="rtsp://localhost:8554/cam1",
                ptz_enabled=True,
            ),
        ],
    )


def test_ptz_endpoint_requires_api_key(base_config) -> None:
    """TR-13: PTZ endpoint requires API key."""
    app = create_app(config=base_config)
    client = TestClient(app)
    response = client.post("/api/cameras/cam1/ptz", json={"action": "move"})
    assert response.status_code == 401


def test_ptz_endpoint_validates_enabled(base_config) -> None:
    """TR-14: Disabled camera returns 403."""
    app = create_app(config=base_config)
    app.state.api_key = "test-key"
    client = TestClient(app)
    response = client.post(
        "/api/cameras/cam1/ptz",
        json={"action": "move"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 403


def test_ptz_endpoint_unsupported_returns_501(ptz_enabled_config) -> None:
    """TR-15: Unsupported PTZ returns 501."""
    app = create_app(config=ptz_enabled_config)
    app.state.api_key = "test-key"
    # Remove adapter support
    app.state.ptz_service = PtzControlService(
        cameras=ptz_enabled_config.cameras,
        ptz_adapter=None,
        audit_logger=app.state.audit_logger,
    )
    client = TestClient(app)
    response = client.post(
        "/api/cameras/cam1/ptz",
        json={"action": "move"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 501
