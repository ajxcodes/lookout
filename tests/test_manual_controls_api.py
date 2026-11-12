"""Integration tests for manual camera controls and settings."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig
from lookout.domain.ptz_service import PtzControlService


@pytest.fixture
def config(tmp_path) -> LookoutConfig:
    return LookoutConfig(
        system_name="Manual Controls Test",
        storage=StorageConfig(base_path=str(tmp_path / "data")),
        cameras=[
            CameraConfig(
                id="cam1",
                name="Cam 1",
                stream_url="rtsp://localhost:8554/cam1",
            ),
        ],
    )


def test_manual_snapshot_endpoint(config) -> None:
    """TR-18: Manual snapshot endpoint returns 200."""
    app = create_app(config=config)
    app.state.api_key = "test-key"
    client = TestClient(app)
    response = client.post("/api/cameras/cam1/snapshot", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200


def test_manual_record_endpoint(config) -> None:
    """TR-19: Manual record endpoint returns 200."""
    app = create_app(config=config)
    app.state.api_key = "test-key"
    client = TestClient(app)
    response = client.post("/api/cameras/cam1/record", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200


def test_patch_camera_settings_persisted(config) -> None:
    """TR-20: PATCH camera settings persists enabled=False."""
    app = create_app(config=config)
    app.state.api_key = "test-key"
    client = TestClient(app)
    response = client.patch(
        "/api/cameras/cam1/settings",
        json={"enabled": False},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_manual_controls_audit_entry(config) -> None:
    """TR-21: Manual control creates audit entry with manual_ action."""
    audit_logger = AuditLogger(db_path=":memory:")
    cam = config.cameras[0]
    service = PtzControlService(cameras=[cam], audit_logger=audit_logger)
    await service.capture_snapshot("cam1")
    audit_entries = await audit_logger.query()
    audit_entry = audit_entries[0]
    assert audit_entry["action"].startswith("manual_")
