import pytest

from lookout.config.schema import LookoutConfig
from lookout.domain.native_ingestion_service import NativeIngestionService


@pytest.mark.asyncio
async def test_native_ingestion_adopts_discovered_cameras() -> None:
    config = LookoutConfig()
    service = NativeIngestionService(config=config)
    await service.adopt_discovered_cameras(
        [{"id": "cam1", "name": "Cam", "camera_type": "wyze", "provider": "wyze"}]
    )
    assert len(config.cameras) == 1


@pytest.mark.asyncio
async def test_native_ingestion_replaces_duplicate_camera() -> None:
    config = LookoutConfig()
    service = NativeIngestionService(config=config)
    await service.adopt_discovered_cameras(
        [{"id": "cam1", "name": "Old", "camera_type": "wyze", "provider": "wyze"}]
    )
    await service.adopt_discovered_cameras(
        [{"id": "cam1", "name": "New", "camera_type": "wyze", "provider": "wyze"}]
    )

    assert len(config.cameras) == 1
    assert config.cameras[0].name == "New"
