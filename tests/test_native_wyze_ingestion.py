import pytest

from lookout.adapters.wyze_native import WyzeNativeAdapter, WyzeTutkPtzAdapter
from lookout.ports.egress import VideoRecorderPort


def test_wyze_native_implements_video_recorder_port() -> None:
    adapter = WyzeNativeAdapter()
    assert isinstance(adapter, VideoRecorderPort)


@pytest.mark.asyncio
async def test_wyze_native_capture_snapshot() -> None:
    adapter = WyzeNativeAdapter()
    snapshot_path = await adapter.capture_snapshot("cam1")
    assert snapshot_path.endswith(".jpg")


@pytest.mark.asyncio
async def test_wyze_native_record_clip() -> None:
    adapter = WyzeNativeAdapter()
    clip_path = await adapter.record_clip("cam1", 1.0)
    assert clip_path.endswith(".mp4")


@pytest.mark.asyncio
async def test_wyze_native_ptz_move() -> None:
    adapter = WyzeNativeAdapter()
    await adapter.move("cam1", 0.1, 0.2, 0.3, 0.5)
    assert adapter.last_command == "move"
    # Also exercise TUTK adapter.
    tutk = WyzeTutkPtzAdapter()
    await tutk.move("cam1", 0.1, 0.2, 0.3, 0.5)
    assert tutk.last_command == "move"
