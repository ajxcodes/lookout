"""Unit tests for Ring cloud video recorder and snapshot adapter."""

import pytest
import respx

from lookout.adapters.ring import RingAdapter
from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import RingConfig
from lookout.ports.egress import VideoRecorderPort


@pytest.mark.asyncio
async def test_ring_implements_video_recorder_port() -> None:
    """TR-8: Verify RingAdapter implements VideoRecorderPort protocol."""
    adapter = RingAdapter()
    assert isinstance(adapter, VideoRecorderPort)


@pytest.mark.asyncio
@respx.mock
async def test_ring_record_clip_success(tmp_path: pytest.TempPathFactory) -> None:
    """TR-5: Verify resolving and downloading Ring recording clip succeeds."""
    config = RingConfig(auth_token="dummy-token", api_url="https://api.ring.com")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = RingAdapter(config=config, temp_manager=manager)

    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/recording").respond(
        status_code=200,
        headers={"content-type": "video/mp4"},
        content=b"mock-ring-cloud-video-data",
    )

    clip_path = await adapter.record_clip("doorbell_1", 10.0)
    assert clip_path.endswith(".mp4")


@pytest.mark.asyncio
@respx.mock
async def test_ring_capture_snapshot_success(tmp_path: pytest.TempPathFactory) -> None:
    """TR-6: Verify fetching cloud snapshot returns JPEG."""
    config = RingConfig(auth_token="dummy-token", api_url="https://api.ring.com")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = RingAdapter(config=config, temp_manager=manager)

    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/snapshot").respond(
        status_code=200,
        headers={"content-type": "image/jpeg"},
        content=b"\xff\xd8\xff\xe0mock-jpeg-snapshot",
    )

    snapshot_path = await adapter.capture_snapshot("ring://doorbell_1")
    assert snapshot_path.endswith(".jpg")


@pytest.mark.asyncio
@respx.mock
async def test_ring_record_clip_fallback_to_snapshot(tmp_path: pytest.TempPathFactory) -> None:
    """TR-7: Verify unavailable cloud video falls back to snapshot."""
    config = RingConfig(auth_token="dummy-token", api_url="https://api.ring.com")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = RingAdapter(config=config, temp_manager=manager)

    # Both recording endpoint and history return 404/empty
    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/recording").respond(
        status_code=404
    )
    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/history").respond(
        status_code=200, json=[]
    )
    # Snapshot succeeds
    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/snapshot").respond(
        status_code=200,
        headers={"content-type": "image/jpeg"},
        content=b"\xff\xd8\xff\xe0mock-fallback-snapshot",
    )

    result = await adapter.record_clip("ring://doorbell_1", 10.0)
    assert result.endswith(".jpg")


@pytest.mark.asyncio
@respx.mock
async def test_ring_history_resolution_download(tmp_path: pytest.TempPathFactory) -> None:
    """Verify video downloaded via history ding query when direct recording is 404."""
    config = RingConfig(auth_token="token", api_url="https://api.ring.com")
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = RingAdapter(config=config, temp_manager=manager)

    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/recording").respond(
        status_code=404
    )
    respx.get("https://api.ring.com/clients_api/doorbots/doorbell_1/history").respond(
        status_code=200, json=[{"id": 98765, "created_at": "2026-11-06T00:00:00Z"}]
    )
    respx.get("https://api.ring.com/clients_api/dings/98765/recording").respond(
        status_code=200,
        headers={"content-type": "video/mp4"},
        content=b"mock-ding-recording",
    )

    clip_path = await adapter.record_clip("doorbell_1", 10.0)
    assert clip_path.endswith(".mp4")
