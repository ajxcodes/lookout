"""Tests for per-camera HA settings."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from auth_test_helpers import make_client

from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig


@pytest.mark.asyncio
async def test_patch_camera_ha_fields_persists(tmp_path) -> None:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[CameraConfig(id="cam1", name="Cam", stream_url="rtsp://example/cam")],
    )
    client, app, _ = make_client(tmp_path, config)
    # patch ha_sync to avoid
    app.state.ha_sync = MagicMock()
    resp = client.patch(
        "/api/cameras/cam1/settings", json={"ha_enabled": True, "ha_entity_name": "Porch Cam"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ha_enabled") is True or data.get("camera", {}).get("ha_enabled") is True
    cam = next(c for c in config.cameras if c.id == "cam1")
    assert getattr(cam, "ha_enabled", None) is True


def test_remove_camera_triggers_ha_cleanup(tmp_path) -> None:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[CameraConfig(id="cam1", name="Cam", stream_url="rtsp://example/cam")],
    )
    client, app, _ = make_client(tmp_path, config)
    mock_mgr = MagicMock()
    app.state.ha_sync = mock_mgr
    resp = client.delete("/api/cameras/cam1")
    assert resp.status_code == 200
    mock_mgr.remove_camera.assert_called()
