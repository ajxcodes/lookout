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
