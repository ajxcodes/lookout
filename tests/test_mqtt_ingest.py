"""Tests for native MQTT event ingestion and ntfy notification links."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lookout.adapters.mqtt_ingest import MqttEventIngestor
from lookout.adapters.ntfy import NtfyDispatcher
from lookout.config.schema import CameraConfig, LookoutConfig, MqttConfig
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel

RING_MOTION = "ring/loc/camera/649a637fdc48/motion/state"
RING_SNAPSHOT = "ring/loc/camera/649a637fdc48/snapshot/image"
JPEG = b"\xff\xd8fake\xff\xd9"


def _config(tmp_path: Any) -> LookoutConfig:
    return LookoutConfig(
        cameras=[
            CameraConfig(
                id="front_door",
                name="Front Door",
                stream_url="rtsp://127.0.0.1/front",
                mqtt_id="649a637fdc48",
            ),
            CameraConfig(id="mochi_crate_cam", name="Crate", stream_url="rtsp://127.0.0.1/c"),
        ],
        mqtt=MqttConfig(enabled=True, cooldown_seconds=30.0),
        storage={"base_path": str(tmp_path), "database_path": str(tmp_path / "db.sqlite")},
    )


def _event(camera_id: str) -> CameraEvent:
    return CameraEvent(
        id="evt-1",
        camera_id=camera_id,
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Person at door",
        snapshot_uri="/media/events/front_door_motion.jpg",
    )


def _ingestor(config: LookoutConfig, calls: list[tuple[str, dict[str, Any]]]) -> MqttEventIngestor:
    async def ingest(camera_id: str, payload: dict[str, Any]) -> CameraEvent:
        calls.append((camera_id, payload))
        return _event(camera_id)

    ing = MqttEventIngestor(config, ingest=ingest)
    ing._ready_at = 0.0
    return ing


async def test_ring_motion_triggers_event_with_cached_snapshot(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    await ing.handle_message(RING_SNAPSHOT, JPEG)
    await ing.handle_message(RING_MOTION, b"ON")

    assert len(calls) == 1
    camera_id, payload = calls[0]
    assert camera_id == "front_door"
    assert payload["image"] == JPEG
    assert payload["snapshot_uri"].startswith("/media/events/front_door_motion_")
    assert payload["metadata"]["trigger"] == "motion"
    saved = tmp_path / "snapshots" / "events" / payload["snapshot_uri"].rsplit("/", 1)[1]
    assert saved.read_bytes() == JPEG


async def test_off_state_and_cooldown_are_ignored(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    await ing.handle_message(RING_SNAPSHOT, JPEG)
    await ing.handle_message(RING_MOTION, b"OFF")
    await ing.handle_message(RING_MOTION, b"ON")
    await ing.handle_message(RING_MOTION, b"ON")
    assert len(calls) == 1


async def test_retained_grace_period_suppresses_events(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    ing._ready_at = float("inf")
    await ing.handle_message(RING_MOTION, b"ON")
    assert calls == []


async def test_wyze_audio_triggers_only_for_pet_sounds(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    ing._go2rtc_frame = lambda cam: _none()  # type: ignore[method-assign,assignment]
    topic = "wyzebridge/mochi_crate_cam/audio"
    await ing.handle_message(topic, json.dumps({"detection": "idle"}).encode())
    await ing.handle_message(topic, json.dumps({"detection": "bark"}).encode())

    assert [c[0] for c in calls] == ["mochi_crate_cam"]
    assert calls[0][1]["metadata"]["sound"] == "bark"


async def _none() -> None:
    return None


async def test_unknown_camera_is_ignored(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    await ing.handle_message("ring/loc/camera/deadbeef/motion/state", b"ON")
    assert calls == []


async def test_ntfy_uses_absolute_attach_and_ha_panel_links() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = NtfyDispatcher(
        destination="http://192.168.1.3:8089/cameras",
        deep_link_base_url="https://ha.example/lookout/viewer",
        media_base_url="https://lookout.example",
        client=client,
    )
    ok = await dispatcher.send_notification(_event("front_door"), target="default")

    assert ok is True
    assert captured["attach"] == "https://lookout.example/media/events/front_door_motion.jpg"
    expected = "https://ha.example/lookout/viewer?camera_id=front_door&event_id=evt-1"
    assert captured["click"] == expected
    assert expected in captured["actions"]


async def test_ntfy_companion_app_deep_links() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = NtfyDispatcher(
        destination="http://192.168.1.3:8089/cameras",
        deep_link_base_url="https://ha.example/lookout/viewer",
        media_base_url="https://lookout.example",
        use_companion_app=True,
        client=client,
    )
    ok = await dispatcher.send_notification(_event("front_door"), target="default")

    assert ok is True
    app_link = "homeassistant://navigate/lookout/viewer?camera_id=front_door&event_id=evt-1"
    assert captured["click"] == app_link
    assert app_link in captured["actions"]
    assert "https://lookout.example/?camera_id=front_door&event_id=evt-1" in captured["actions"]
    assert "view, Live Stream" in captured["actions"]


@pytest.mark.parametrize("flag", [True, False])
def test_mqtt_defaults(flag: bool) -> None:
    assert LookoutConfig().mqtt.enabled is False
    assert MqttConfig(enabled=flag).enabled is flag


async def test_mqtt_records_background_clip_and_invokes_on_clip_ready(tmp_path: Path) -> None:
    cfg = LookoutConfig()
    cfg.storage.base_path = str(tmp_path)
    cfg.go2rtc.api_url = "http://127.0.0.1:1984"

    clip_ready_events: list[CameraEvent] = []

    async def _on_clip(evt: CameraEvent) -> None:
        clip_ready_events.append(evt)

    ingestor = MqttEventIngestor(
        config=cfg,
        ingest=AsyncMock(),
        on_clip_ready=_on_clip,
    )

    event = _event("backyard")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/stream.mp4"
        assert "backyard" in request.url.params["src"]
        return httpx.Response(200, content=b"\x00" * 2048)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    with patch("lookout.adapters.mqtt_ingest.httpx.AsyncClient", return_value=mock_client):
        await ingestor._record_clip_background("backyard", event, duration=5.0)

    assert len(clip_ready_events) == 1
    assert event.media_uri == f"/media/events/backyard_{event.id}.mp4"
    saved_file = tmp_path / "events" / f"backyard_{event.id}.mp4"
    assert saved_file.is_file()
    assert saved_file.stat().st_size == 2048


async def test_ntfy_multi_object_and_threat_emojis() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = NtfyDispatcher(
        destination="http://192.168.1.3:8089/cameras",
        deep_link_base_url="https://ha.example/lookout/viewer",
        media_base_url="https://lookout.example",
        client=client,
    )
    from lookout.domain.models import BoundingBox, DetectionMetrics

    event = _event("backyard")
    event.threat_level = ThreatLevel.HIGH
    event.metrics = DetectionMetrics(
        confidence=0.88,
        bounding_boxes=[
            BoundingBox(label="person", confidence=0.88, x_min=0, y_min=0, x_max=10, y_max=10),
            BoundingBox(label="car", confidence=0.79, x_min=10, y_min=10, x_max=20, y_max=20),
        ],
    )
    ok = await dispatcher.send_notification(event, target="default")
    assert ok is True
    assert captured.get("x-sequence-id") == event.id
    assert captured.get("sequence-id") == event.id
    title = captured["title"]
    assert title.startswith("=?utf-8?B?")
    import base64

    decoded = base64.b64decode(title.split("?")[3]).decode("utf-8")
    assert "⚠️ [HIGH]" in decoded
    assert "👤 Person" in decoded
    assert "🚗 Car" in decoded
    assert "on Backyard" in decoded


async def test_wyze_detection_topic_and_boolean_payloads(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ing = _ingestor(_config(tmp_path), calls)
    ing._go2rtc_frame = lambda cam: _none()  # type: ignore[method-assign,assignment]

    # Non-dict or boolean state payloads (from docker-wyze-bridge) must be safely ignored
    await ing.handle_message("wyzebridge/mochi_crate_cam/audio", b"true")
    await ing.handle_message("wyzebridge/mochi_crate_cam/audio", b"false")
    await ing.handle_message("wyzebridge/mochi_crate_cam/detection", b"123")
    assert calls == []

    # Valid detection JSON on either /audio or /detection topics must trigger
    topic_detection = "wyzebridge/mochi_crate_cam/detection"
    await ing.handle_message(topic_detection, json.dumps({"detection": "bark", "confidence": 0.85}).encode())
    assert len(calls) == 1
    assert calls[0][0] == "mochi_crate_cam"
    assert calls[0][1]["metadata"]["sound"] == "bark"


async def test_on_demand_clip_recording_retries_and_succeeds(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.go2rtc.api_url = "http://127.0.0.1:1984"

    clip_ready_events: list[CameraEvent] = []

    async def _on_clip(evt: CameraEvent) -> None:
        clip_ready_events.append(evt)

    ingestor = MqttEventIngestor(
        config=cfg,
        ingest=AsyncMock(),
        on_clip_ready=_on_clip,
    )

    event = _event("front_door")
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # First attempt fails with cold stream (empty/insufficient data)
            return httpx.Response(200, content=b"short")
        # Subsequent attempt succeeds once stream settles
        return httpx.Response(200, content=b"\x00" * 4096)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    with patch("lookout.adapters.mqtt_ingest.httpx.AsyncClient", return_value=mock_client):
        with patch("asyncio.sleep", AsyncMock()):  # Avoid test delays
            await ingestor._record_clip_background("front_door", event, duration=5.0)

    assert attempts >= 2
    assert len(clip_ready_events) == 1
    assert event.media_uri == f"/media/events/front_door_{event.id}.mp4"
    saved = tmp_path / "events" / f"front_door_{event.id}.mp4"
    assert saved.is_file()
    assert saved.stat().st_size == 4096


async def test_go2rtc_frame_retries_for_on_demand_cameras(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.go2rtc.api_url = "http://127.0.0.1:1984"
    ingestor = MqttEventIngestor(config=cfg, ingest=AsyncMock())

    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, content=b"stream activating")
        return httpx.Response(200, content=JPEG)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(mock_handler))

    with patch("lookout.adapters.mqtt_ingest.httpx.AsyncClient", side_effect=client_factory):
        with patch("asyncio.sleep", AsyncMock()):
            frame = await ingestor._go2rtc_frame("front_door")

    assert attempts == 2
    assert frame == JPEG
