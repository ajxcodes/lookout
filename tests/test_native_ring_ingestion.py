import pytest

from lookout.adapters.ring_native import RingNativeAdapter
from lookout.ports.egress import VideoRecorderPort


def test_ring_native_implements_video_recorder_port() -> None:
    adapter = RingNativeAdapter()
    assert isinstance(adapter, VideoRecorderPort)


@pytest.mark.asyncio
async def test_ring_native_capture_snapshot() -> None:
    adapter = RingNativeAdapter()
    snapshot_path = await adapter.capture_snapshot("ring://doorbell_1")
    assert snapshot_path.endswith(".jpg")


@pytest.mark.asyncio
async def test_ring_native_record_clip() -> None:
    adapter = RingNativeAdapter()
    clip_path = await adapter.record_clip("doorbell_1", 10.0)
    assert clip_path.endswith(".mp4")


@pytest.mark.asyncio
async def test_ring_native_event_ingestion() -> None:
    adapter = RingNativeAdapter()
    event = await adapter.ingest_event({"camera_id": "ring_doorbell_1"})
    assert event.camera_id == "ring_doorbell_1"
