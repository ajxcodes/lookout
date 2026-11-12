"""Real-code-path tests for native Ring ingestion."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from lookout.adapters.native_stream_manager import MediaCaptureError
from lookout.adapters.ring_native import (
    RingAuthError,
    RingNativeAdapter,
    RingStreamSourceError,
    decode_ring_token,
    encode_ring_token,
)
from lookout.adapters.ring_native import (
    _parse_datetime as _ring_parse_datetime,
)
from lookout.config.schema import CameraConfig, RingConfig
from lookout.domain.models import EventCategory
from lookout.ports.egress import VideoRecorderPort

JPEG = b"\xff\xd8\xff\xe0" + b"ring-jpeg-payload" * 64
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08mdat" + b"\x00" * 256

INNER_TOKEN = {"rt": "refresh-token/with+chars", "hid": "hardware-id-1", "pnc": "pnc"}
RAW_TOKEN = encode_ring_token(INNER_TOKEN)


class FakeStreamManager:
    def __init__(
        self,
        frame_bytes: bytes = JPEG,
        clip_bytes: bytes = MP4,
        frame_error: Exception | None = None,
        clip_error: Exception | None = None,
    ) -> None:
        self.frame_bytes = frame_bytes
        self.clip_bytes = clip_bytes
        self.frame_error = frame_error
        self.clip_error = clip_error
        self.ensured: list[tuple[str, str | None]] = []

    async def ensure_stream(self, camera_id: str, source: str | None = None) -> None:
        self.ensured.append((camera_id, source))

    async def frame(self, camera_id: str) -> bytes:
        if self.frame_error is not None:
            raise self.frame_error
        return self.frame_bytes

    async def clip(self, camera_id: str, duration_seconds: float) -> bytes:
        if self.clip_error is not None:
            raise self.clip_error
        return self.clip_bytes


class FakeRingDevice:
    device_api_id = 410964110
    device_id = "649a637fdc48"
    name = "Front Door"
    model = "doorbell_v4"
    kind = "doorbell"
    family = "doorbots"
    connection_status = "online"

    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        self.history = history or []
        self.history_calls: list[dict[str, Any]] = []

    async def async_history(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.history_calls.append(kwargs)
        return list(self.history)


class FakeRingClient:
    def __init__(self, devices: list[FakeRingDevice], fail: bool = False) -> None:
        self.device_list = devices
        self.fail = fail

    def devices(self) -> Any:
        if self.fail:
            raise RuntimeError("ring auth failed")
        return SimpleNamespace(video_devices=list(self.device_list))

    async def async_update_devices(self) -> None:
        if self.fail:
            raise RuntimeError("ring auth failed")


def _camera(camera_id: str = "front_door") -> CameraConfig:
    return CameraConfig(
        id=camera_id,
        name="Front Door",
        stream_url=(
            "ring:?camera_id=410964110&device_id=649a637fdc48"
            f"&refresh_token={quote(RAW_TOKEN, safe='')}"
        ),
        native_provider="ring",
        native_device_id="410964110",
    )


def test_ring_adapter_implements_video_recorder_port() -> None:
    assert isinstance(RingNativeAdapter(config=RingConfig()), VideoRecorderPort)


def test_token_roundtrip() -> None:
    assert decode_ring_token(RAW_TOKEN) == INNER_TOKEN
    with pytest.raises(RingAuthError):
        decode_ring_token("not-base64")


def test_build_stream_source_uses_full_encoded_token() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    device = {
        "device_api_id": 410964110,
        "device_id": "649a637fdc48",
        "name": "Front Door",
    }

    source = adapter.build_stream_source(device)
    snapshot = adapter.build_stream_source(device, snapshot=True)

    assert source == (
        "ring:?camera_id=410964110&device_id=649a637fdc48"
        f"&refresh_token={quote(RAW_TOKEN, safe='')}"
    )
    assert snapshot == source + "&snapshot"


def test_build_stream_source_requires_device_fields() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    with pytest.raises(RingStreamSourceError):
        adapter.build_stream_source({"device_api_id": 1})


async def test_capture_snapshot_uses_snapshot_source_and_writes_jpeg(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    path = await adapter.capture_snapshot("front_door")

    assert path.endswith(".jpg")
    assert Path(path).read_bytes() == JPEG
    assert manager.ensured[0][0] == "front_door"
    assert manager.ensured[0][1].endswith("&snapshot")


async def test_capture_snapshot_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(frame_error=MediaCaptureError("empty frame"))
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    with pytest.raises(MediaCaptureError):
        await adapter.capture_snapshot("front_door")
    assert list(tmp_path.iterdir()) == []


async def test_record_clip_writes_mp4(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    path = await adapter.record_clip("front_door", 5.0)

    assert path.endswith(".mp4")
    data = Path(path).read_bytes()
    assert data == MP4
    assert b"ftyp" in data[:64]
    assert not manager.ensured[0][1].endswith("&snapshot")


async def test_record_clip_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(clip_error=MediaCaptureError("no mp4"))
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        stream_manager=manager,
        cameras=[_camera()],
    )

    with pytest.raises(MediaCaptureError):
        await adapter.record_clip("front_door", 5.0)
    assert list(tmp_path.iterdir()) == []


async def test_discover_and_test_connection_use_real_device_list() -> None:
    fake = FakeRingClient([FakeRingDevice()])
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN), client=fake)

    devices = await adapter.discover_devices()
    result = await adapter.test_connection()

    assert devices[0]["id"] == "410964110"
    assert devices[0]["device_id"] == "649a637fdc48"
    assert devices[0]["stream_source"].startswith("ring:?camera_id=410964110")
    assert result["reachable"] is True
    assert len(result["devices"]) == 1


async def test_connection_reports_failure_instead_of_claiming_reachable() -> None:
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([], fail=True),
    )

    result = await adapter.test_connection()

    assert result["reachable"] is False
    assert "ring auth failed" in (result["error"] or "")


async def test_poll_events_maps_real_history_fields() -> None:
    created = datetime.now(UTC) - timedelta(minutes=1)
    device = FakeRingDevice(
        history=[
            {
                "id": 987654,
                "created_at": created,
                "kind": "motion",
                "answered": True,
                "cv_properties": {"detection_type": "human"},
                "recording": {"status": "ready"},
                "snapshot_url": "",
            }
        ]
    )
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([device]),
        cameras=[_camera()],
    )

    payloads = await adapter.poll_events("front_door", since=created - timedelta(seconds=5))

    assert payloads[0]["event_id"] == "987654"
    assert payloads[0]["camera_id"] == "front_door"
    assert payloads[0]["kind"] == "motion"
    assert payloads[0]["detection_type"] == "human"
    assert payloads[0]["answered"] is True
    assert payloads[0]["timestamp"] == created.isoformat()
    assert device.history_calls == [{"limit": 20}]


async def test_poll_events_filters_by_since() -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    device = FakeRingDevice(
        history=[{"id": 1, "created_at": old, "kind": "motion", "cv_properties": {}}]
    )
    adapter = RingNativeAdapter(
        config=RingConfig(refresh_token=RAW_TOKEN),
        client=FakeRingClient([device]),
        cameras=[_camera()],
    )

    assert await adapter.poll_events("front_door", since=datetime.now(UTC)) == []


async def test_ingest_event_requires_real_payload() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))

    with pytest.raises(ValueError):
        await adapter.ingest_event(None)
    with pytest.raises(ValueError):
        await adapter.ingest_event({})


async def test_ingest_event_builds_camera_event_from_payload() -> None:
    adapter = RingNativeAdapter(config=RingConfig(refresh_token=RAW_TOKEN))
    payload = {
        "event_id": "987654",
        "camera_id": "front_door",
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": "motion",
        "detection_type": "human",
        "metadata": {"provider": "ring"},
    }

    event = await adapter.ingest_event(payload)

    assert event.id == "ring-987654"
    assert event.camera_id == "front_door"
    assert event.category == EventCategory.UNKNOWN_VISITOR
    assert event.metadata["provider"] == "ring"


def test_parse_datetime_handles_epoch_millis() -> None:
    result = _ring_parse_datetime(1700000000000)
    assert result == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
