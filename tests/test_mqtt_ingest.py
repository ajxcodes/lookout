"""Tests for native MQTT event ingestion and ntfy notification links."""

import json
from datetime import UTC, datetime
from typing import Any

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


@pytest.mark.parametrize("flag", [True, False])
def test_mqtt_defaults(flag: bool) -> None:
    assert LookoutConfig().mqtt.enabled is False
    assert MqttConfig(enabled=flag).enabled is flag
