"""Unit tests for go2rtc video recording and snapshot adapter."""

import pytest
import respx

from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import Go2RtcConfig
from lookout.ports.egress import VideoRecorderPort


@pytest.mark.asyncio
async def test_go2rtc_implements_video_recorder_port() -> None:
    """TR-4: Verify Go2RtcAdapter implements VideoRecorderPort protocol."""
    adapter = Go2RtcAdapter()
    assert isinstance(adapter, VideoRecorderPort)


@pytest.mark.asyncio
@respx.mock
async def test_go2rtc_record_clip_success(tmp_path: pytest.TempPathFactory) -> None:
    """TR-1: Verify successful non-blocking stream capture returns MP4 file."""
    config = Go2RtcConfig(api_url="http://localhost:1984")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = Go2RtcAdapter(config=config, temp_manager=manager)

    respx.get("http://localhost:1984/api/stream.mp4").respond(
        status_code=200, content=b"mock-mp4-video-stream-bytes"
    )

    clip_path = await adapter.record_clip("cam_front", 5.0)
    assert clip_path.endswith(".mp4")


@pytest.mark.asyncio
@respx.mock
async def test_go2rtc_capture_snapshot_success(tmp_path: pytest.TempPathFactory) -> None:
    """TR-2: Verify snapshot extraction fetches JPEG frame."""
    config = Go2RtcConfig(api_url="http://localhost:1984")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = Go2RtcAdapter(config=config, temp_manager=manager)

    respx.get("http://localhost:1984/api/frame.jpeg").respond(
        status_code=200, content=b"\xff\xd8\xff\xe0mock-jpeg-bytes"
    )

    snapshot_path = await adapter.capture_snapshot("cam_front")
    assert snapshot_path.endswith(".jpg")


@pytest.mark.asyncio
@respx.mock
async def test_go2rtc_record_clip_fallback_to_snapshot(tmp_path: pytest.TempPathFactory) -> None:
    """TR-3: Verify offline video stream triggers automatic fallback to snapshot."""
    config = Go2RtcConfig(api_url="http://localhost:1984")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = Go2RtcAdapter(config=config, temp_manager=manager)

    # Video stream returns server error / disconnect
    respx.get("http://localhost:1984/api/stream.mp4").respond(status_code=503)
    # Frame endpoint remains available
    respx.get("http://localhost:1984/api/frame.jpeg").respond(
        status_code=200, content=b"\xff\xd8\xff\xe0mock-fallback-frame"
    )

    result = await adapter.record_clip("cam_front", 5.0)
    assert result.endswith(".jpg")


@pytest.mark.asyncio
@respx.mock
async def test_go2rtc_stream_timeout_fallback(tmp_path: pytest.TempPathFactory) -> None:
    """Verify network timeout triggers fallback snapshot."""
    config = Go2RtcConfig(api_url="http://localhost:1984", timeout_seconds=0.1)
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = Go2RtcAdapter(config=config, temp_manager=manager)

    import httpx

    respx.get("http://localhost:1984/api/stream.mp4").mock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )
    respx.get("http://localhost:1984/api/frame.jpeg").respond(
        status_code=200, content=b"\xff\xd8\xff\xe0mock-frame"
    )

    result = await adapter.record_clip("cam_front", 5.0)
    assert result.endswith(".jpg")
