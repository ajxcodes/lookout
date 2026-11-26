"""Tests for the native Wyze/Ring event poller."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from lookout.adapters.native_event_poller import NativeEventPoller
from lookout.config.schema import CameraConfig, LookoutConfig

JPEG = b"\xff\xd8\xff\xe0" + b"poller-jpeg" * 64


class FakeFrameManager:
    def __init__(self) -> None:
        self.streams: dict[str, str] = {}
        self.frame_calls: list[str] = []

    async def frame(self, camera_id: str) -> bytes:
        self.frame_calls.append(camera_id)
        return JPEG


class FakeAdapter:
    def __init__(
        self,
        events: dict[str, list[dict[str, Any]]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events or {}
        self.error = error
        self.since_calls: list[datetime | None] = []

    async def poll_events(
        self, camera_id: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        self.since_calls.append(since)
        if self.error is not None:
            raise self.error
        return list(self.events.get(camera_id, []))


def _config() -> LookoutConfig:
    return LookoutConfig(
        cameras=[
            CameraConfig(
                id="door",
                name="Door",
                stream_url="ring:?camera_id=1&device_id=abc",
                native_provider="ring",
            ),
            CameraConfig(
                id="office",
                name="Office",
                stream_url="wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x",
                native_provider="wyze",
            ),
            CameraConfig(id="rtsp", name="RTSP", stream_url="rtsp://example/cam"),
        ]
    )


def _events() -> list[dict[str, Any]]:
    return [
        {"event_id": "evt-1", "camera_id": "door", "timestamp": "2026-01-01T00:00:00+00:00"},
        {"event_id": "evt-2", "camera_id": "door", "timestamp": "2026-01-01T00:01:00+00:00"},
    ]


async def test_poller_ingests_each_event_once_and_dedupes() -> None:
    adapter = FakeAdapter({"door": _events()})
    other = FakeAdapter()
    manager = FakeFrameManager()
    manager.streams["door"] = "ring:?camera_id=1&device_id=abc&snapshot"
    ingested: list[tuple[str, dict[str, Any]]] = []
    broadcast: list[Any] = []

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ingested.append((camera_id, payload))
        return {"id": f"event-{len(ingested)}", "camera_id": camera_id}

    async def on_event(event: Any) -> None:
        broadcast.append(event)

    poller = NativeEventPoller(
        config=_config(),
        wyze=other,
        ring=adapter,
        ingest=ingest,
        on_event=on_event,
        stream_manager=manager,
        interval=60.0,
    )

    await poller._tick()
    assert len(ingested) == 2
    assert len(broadcast) == 2
    assert ingested[0][0] == "door"
    assert ingested[0][1]["image"] == JPEG
    assert ingested[0][1]["metadata"]["event_id"] == "evt-1"
    assert manager.frame_calls == ["door", "door"]

    # Second tick returns the same events: nothing is re-ingested.
    await poller._tick()
    assert len(ingested) == 2


async def test_poller_survives_adapter_exception() -> None:
    adapter = FakeAdapter({"door": _events()})
    other = FakeAdapter()
    ingested: list[str] = []

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ingested.append(camera_id)
        return {"id": "x"}

    poller = NativeEventPoller(config=_config(), wyze=other, ring=adapter, ingest=ingest)
    await poller._tick()
    assert len(ingested) == 2

    adapter.error = RuntimeError("ring history 500")
    await poller._tick()
    assert len(ingested) == 2


async def test_poller_ingests_metadata_when_snapshot_fails() -> None:
    class BrokenFrames(FakeFrameManager):
        async def frame(self, camera_id: str) -> bytes:
            raise RuntimeError("go2rtc frame empty")

    adapter = FakeAdapter({"door": _events()})
    other = FakeAdapter()
    manager = BrokenFrames()
    manager.streams["door"] = "ring:?camera_id=1&device_id=abc&snapshot"
    ingested: list[dict[str, Any]] = []

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ingested.append(payload)
        return {"id": "x"}

    poller = NativeEventPoller(
        config=_config(),
        wyze=other,
        ring=adapter,
        ingest=ingest,
        stream_manager=manager,
    )

    await poller._tick()

    assert len(ingested) == 2
    assert "image" not in ingested[0]


async def test_poller_consecutive_failures_resets_after_success() -> None:
    """TR-15 (AC6): failures increment, a clean tick resets to zero."""
    adapter = FakeAdapter()
    other = FakeAdapter()

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "x"}

    poller = NativeEventPoller(config=_config(), wyze=other, ring=adapter, ingest=ingest)
    adapter.error = RuntimeError("ring history 500")
    await poller._tick()
    assert poller.consecutive_failures >= 1

    adapter.error = None
    await poller._tick()
    assert poller.consecutive_failures == 0
    assert poller.health["consecutive_failures"] == 0


async def test_poller_backs_off_and_stops_spamming_after_repeated_failures(
    caplog: Any,
) -> None:
    """TR-11: backoff limits rapid ticks to one call and one WARNING."""
    ring = FakeAdapter()
    ring.error = RuntimeError("ring history 500")
    wyze = FakeAdapter()

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "x"}

    poller = NativeEventPoller(
        config=_config(),
        wyze=wyze,
        ring=ring,
        ingest=ingest,
        failure_backoff_seconds=1,
    )
    with caplog.at_level("WARNING"):
        for _ in range(3):
            await poller._tick()

    assert len(ring.since_calls) == 1
    health = poller.status()["cameras"]["door"]
    assert health["next_retry_at"] > datetime.now(UTC)
    assert health["retry_after_seconds"] > 0
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "Native event poll failed" in record.getMessage()
    ]
    assert len(warnings) == 1


async def test_poller_error_is_redacted_and_actionable() -> None:
    """TR-12: credential material in adapter errors never reaches status."""
    ring = FakeAdapter()
    ring.error = RuntimeError("refresh_token=SECRET Bearer SECRET")
    wyze = FakeAdapter()

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "x"}

    poller = NativeEventPoller(config=_config(), wyze=wyze, ring=ring, ingest=ingest)
    await poller._tick()

    status = poller.status()
    for camera_id in ("door", "office"):
        last_error = status["cameras"][camera_id]["last_error"] or ""
        assert "SECRET" not in last_error


async def test_poller_start_uses_bounded_first_lookback_and_stops() -> None:
    adapter = FakeAdapter()
    other = FakeAdapter()

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "x"}

    poller = NativeEventPoller(
        config=_config(), wyze=other, ring=adapter, ingest=ingest, interval=60.0
    )
    poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert poller.running is False
    assert adapter.since_calls
    first_since = adapter.since_calls[0]
    assert first_since is not None
    now = datetime.now(UTC)
    assert timedelta(seconds=55) < now - first_since < timedelta(seconds=65)


async def test_poller_two_pass_records_10s_clip_and_invokes_on_clip_ready(
    tmp_path: Any,
) -> None:
    """TR-5: Verify poller records 10s background clip in Pass 2 and invokes on_clip_ready."""
    from pathlib import Path

    mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mdat" + b"clipdata" * 128

    class ClipCapableAdapter(FakeAdapter):
        def __init__(self, events: dict[str, list[dict[str, Any]]]) -> None:
            super().__init__(events)
            self.record_calls: list[tuple[str, float]] = []

        async def record_clip(self, camera_id: str, duration_seconds: float = 10.0) -> str:
            self.record_calls.append((camera_id, duration_seconds))
            clip_file = Path(tmp_path) / f"temp_{camera_id}.mp4"
            clip_file.write_bytes(mp4_bytes)
            return str(clip_file)

    cfg = _config()
    cfg.storage.base_path = str(tmp_path)
    adapter = ClipCapableAdapter(
        {
            "door": [
                {
                    "event_id": "evt-clip-1",
                    "camera_id": "door",
                    "kind": "motion",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            ]
        }
    )
    other = FakeAdapter()
    manager = FakeFrameManager()
    manager.streams["door"] = "ring:?camera_id=1&device_id=abc&snapshot"

    pass1_events: list[dict[str, Any]] = []
    clip_ready_events: list[dict[str, Any]] = []
    clip_done = asyncio.Event()

    async def ingest(camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "evt-clip-1",
            "camera_id": camera_id,
            "media_uri": None,
            "snapshot_uri": payload.get("snapshot_uri"),
        }

    async def on_event(evt: Any) -> None:
        pass1_events.append(dict(evt))

    async def on_clip_ready(evt: Any) -> None:
        clip_ready_events.append(evt)
        clip_done.set()

    poller = NativeEventPoller(
        config=cfg,
        wyze=other,
        ring=adapter,
        ingest=ingest,
        on_event=on_event,
        on_clip_ready=on_clip_ready,
        stream_manager=manager,
        record_clip=True,
        clip_duration=10.0,
        storage_base_path=tmp_path,
    )

    await poller._tick()
    await asyncio.wait_for(clip_done.wait(), timeout=2.0)

    assert len(pass1_events) == 1
    assert pass1_events[0]["media_uri"] is None
    assert adapter.record_calls == [("door", 10.0)]
    assert len(clip_ready_events) == 1
    updated = clip_ready_events[0]
    assert updated["media_uri"] == "/media/events/door_evt-clip-1.mp4"
    saved_mp4 = Path(tmp_path) / "events" / "door_evt-clip-1.mp4"
    assert saved_mp4.is_file()
    assert saved_mp4.read_bytes() == mp4_bytes


async def test_poller_persists_snapshots_to_media_and_updates_latest_camera_snapshot(
    tmp_path: Any,
) -> None:
    """TR-6: Verify _capture_image saves snapshot to event snapshots & updates camera snapshot."""
    from pathlib import Path

    snap_bytes = b"\xff\xd8\xff\xe0" + b"adapter-snap-bytes" * 32

    class SnapshotAdapter(FakeAdapter):
        async def capture_snapshot(self, camera_id: str) -> str:
            scratch = Path(tmp_path) / f"scratch_{camera_id}.jpg"
            scratch.write_bytes(snap_bytes)
            return str(scratch)

    cfg = _config()
    cfg.storage.base_path = str(tmp_path)
    adapter = SnapshotAdapter({"door": _events()[:1]})
    poller = NativeEventPoller(
        config=cfg,
        wyze=FakeAdapter(),
        ring=adapter,
        ingest=lambda cid, p: asyncio.sleep(0),  # type: ignore[arg-type,return-value]
        storage_base_path=tmp_path,
    )

    image, uri = await poller._capture_image("door", "ring", kind="ding")
    assert image == snap_bytes
    assert uri is not None
    assert uri.startswith("/media/events/door_ding_")
    assert uri.endswith(".jpg")

    event_snap_name = uri.rsplit("/", 1)[-1]
    event_snap_file = Path(tmp_path) / "snapshots" / "events" / event_snap_name
    assert event_snap_file.is_file()
    assert event_snap_file.read_bytes() == snap_bytes

    cam_snap_file = Path(tmp_path) / "snapshots" / "door.jpg"
    assert cam_snap_file.is_file()
    assert cam_snap_file.read_bytes() == snap_bytes


async def test_poller_preserves_event_timestamp_and_chronological_order() -> None:
    """TR-10: Verify EventProcessingService.ingest_raw_event preserves event timestamps."""
    from lookout.adapters.memory import InMemoryEventRepository
    from lookout.domain.service import EventProcessingService

    repo = InMemoryEventRepository()
    service = EventProcessingService(repository=repo)

    older_event = await service.ingest_raw_event(
        "door",
        {
            "image": JPEG,
            "metadata": {
                "event_id": "evt-old",
                "timestamp": "2026-03-10T10:00:00Z",
                "kind": "motion",
            },
        },
    )
    newer_event = await service.ingest_raw_event(
        "door",
        {
            "image": JPEG,
            "metadata": {
                "event_id": "evt-new",
                "timestamp": "2026-03-10T10:05:00+00:00",
                "kind": "ding",
            },
        },
    )

    assert older_event.timestamp == datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC)
    assert newer_event.timestamp == datetime(2026, 3, 10, 10, 5, 0, tzinfo=UTC)

    found = await repo.find(camera_id="door")
    assert [e.id for e in found] == [newer_event.id, older_event.id]


async def test_poller_metadata_fallback_when_snapshot_fails() -> None:
    """TR-10: Verify NativeEventPoller ingests metadata-only payloads when snapshot fails."""
    from datetime import UTC, datetime

    from lookout.adapters.native_event_poller import NativeEventPoller
    from lookout.config.schema import CameraConfig, LookoutConfig

    class FakeRingAdapter:
        async def poll_events(self, camera_id: str, since=None):
            return [
                {
                    "event_id": "e-ding",
                    "kind": "ding",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                {
                    "event_id": "e-motion",
                    "kind": "motion",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            ]

        async def capture_snapshot(self, camera_id: str):
            raise TimeoutError("snapshot timed out")

    cfg = LookoutConfig(
        cameras=[
            CameraConfig(
                id="front_door",
                name="Front Door",
                stream_url="ring:?camera_id=front_door",
                native_provider="ring",
                native_device_id="front_door",
            )
        ]
    )
    ingested: list[tuple[str, dict]] = []

    async def fake_ingest(cam_id: str, payload: dict) -> None:
        ingested.append((cam_id, payload))

    poller = NativeEventPoller(config=cfg, wyze=None, ring=FakeRingAdapter(), ingest=fake_ingest)
    await poller._tick()

    assert len(ingested) == 2
    assert ingested[0][1]["metadata"]["kind"] == "ding"
    assert "image" not in ingested[0][1]
