"""Tests for Home Assistant sync manager."""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response

from lookout.adapters.home_assistant_sync import HomeAssistantSyncManager
from lookout.config.schema import CameraConfig


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any, bool]] = []

    def publish(self, topic: str, payload: Any = None, retain: bool = False, **kwargs: Any) -> None:
        self.published.append((topic, payload, bool(retain)))


@pytest.mark.asyncio
@respx.mock
async def test_connection_success_returns_ok() -> None:
    respx.get("http://ha:8123/api/").mock(return_value=Response(200, json={"message": "ok"}))
    manager = HomeAssistantSyncManager(base_url="http://ha:8123", access_token="token")
    result = await manager.test_connection()
    assert result["ok"] is True
    assert result["status"] == 200


@pytest.mark.asyncio
async def test_connection_failure_does_not_raise() -> None:
    manager = HomeAssistantSyncManager(base_url="http://127.0.0.1:1", access_token="token")
    result = await manager.test_connection()
    assert result["ok"] is False
    assert isinstance(result.get("error"), str)


@pytest.mark.asyncio
async def test_sync_camera_publishes_retained_discovery() -> None:
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    result = await manager.sync_camera(camera)
    assert result["ok"] is True
    topics = [t for t, _, _ in mqtt.published]
    assert any(t == "homeassistant/camera/cam1/config" for t in topics)
    camera_payloads = [
        json.loads(p) for (t, p, r) in mqtt.published if t == "homeassistant/camera/cam1/config"
    ]
    assert camera_payloads
    assert "name" in camera_payloads[0]
    assert "configuration_url" in camera_payloads[0]


@pytest.mark.asyncio
async def test_remove_camera_clears_retained_discovery() -> None:
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    await manager.sync_camera(camera)
    published_topics = [t for t, _, _ in mqtt.published]
    mqtt.published.clear()
    result = await manager.remove_camera("cam1")
    assert result["ok"] is True
    cleared_topics = [t for t, _, _ in mqtt.published]
    for topic in published_topics:
        assert topic in cleared_topics
    for _, payload, retain in mqtt.published:
        assert (payload in ("", None, b"")) and retain is True


@pytest.mark.asyncio
async def test_reconcile_on_boot_no_duplicates() -> None:
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt, cameras=[camera]
    )
    await manager.sync_all()
    first_topics = [t for t, _, _ in mqtt.published]
    mqtt.published.clear()
    await manager.sync_all()
    second_topics = [t for t, _, _ in mqtt.published]
    assert set(first_topics) == set(second_topics)


@pytest.mark.asyncio
async def test_sync_failure_does_not_break_pipeline() -> None:
    class BadMqtt:
        def publish(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("boom")

    mqtt = BadMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    result = await manager.sync_camera(camera)
    assert result["ok"] is False
    assert (
        manager.get_status().get("last_error") is not None or result.get("last_error") is not None
    )


@pytest.mark.asyncio
async def test_event_entity_carries_link_attribute() -> None:
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    await manager.sync_camera(camera)
    event_payloads = [json.loads(p) for (t, p, r) in mqtt.published if "event" in t]
    assert event_payloads
    assert any("link" in p or "url" in p for p in event_payloads)
