import pytest

from lookout.adapters.native_stream_manager import NativeStreamManager


@pytest.mark.asyncio
async def test_stream_manager_start_stop() -> None:
    manager = NativeStreamManager()
    await manager.start("cam1", "rtsp://example/cam1")
    await manager.stop("cam1")
    assert manager.running is False
