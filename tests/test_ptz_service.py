"""Unit tests for PTZ control service."""

import pytest

from lookout.adapters.memory.ptz import MockPtzControlAdapter
from lookout.adapters.sqlite.audit import AuditLogger
from lookout.config.schema import CameraConfig
from lookout.domain.ptz_service import PtzControlService


def _camera(enabled: bool = True, max_speed: float = 0.5) -> CameraConfig:
    return CameraConfig(
        id="cam1",
        name="Cam 1",
        stream_url="rtsp://localhost:8554/cam1",
        ptz_enabled=enabled,
        ptz_max_speed=max_speed,
    )


@pytest.mark.asyncio
async def test_ptz_service_rejects_unknown_camera() -> None:
    """TR-9: Unknown camera returns 404."""
    service = PtzControlService(cameras=[], ptz_adapter=MockPtzControlAdapter())
    response = None
    try:
        await service.move("unknown", 0.0, 0.0, 0.0, 0.5)
    except Exception as exc:
        response = exc
    assert response is not None
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ptz_service_rejects_disabled_camera() -> None:
    """TR-10: Disabled camera returns 403."""
    cam = _camera(enabled=False)
    service = PtzControlService(cameras=[cam], ptz_adapter=MockPtzControlAdapter())
    response = None
    try:
        await service.move("cam1", 0.0, 0.0, 0.0, 0.5)
    except Exception as exc:
        response = exc
    assert response is not None
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_ptz_service_clamps_speed_to_max() -> None:
    """TR-11: Requested speed clamped to camera max speed."""
    cam = _camera(enabled=True, max_speed=0.3)
    adapter = MockPtzControlAdapter()
    service = PtzControlService(cameras=[cam], ptz_adapter=adapter)
    await service.move("cam1", 0.0, 0.0, 0.0, 1.0)
    assert adapter.last_speed <= cam.ptz_max_speed


@pytest.mark.asyncio
async def test_ptz_service_audits_command() -> None:
    """TR-12: Every PTZ command recorded in audit log."""
    cam = _camera(enabled=True)
    adapter = MockPtzControlAdapter()
    audit_logger = AuditLogger(db_path=":memory:")
    service = PtzControlService(cameras=[cam], ptz_adapter=adapter, audit_logger=audit_logger)
    await service.move("cam1", 0.0, 0.0, 0.0, 0.5)
    audit_entries = await audit_logger.query()
    assert len(audit_entries) == 1
