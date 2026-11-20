"""Tests for Home Assistant sync manager."""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response

from lookout.adapters.home_assistant_sync import HomeAssistantSyncManager
from lookout.config.schema import CameraConfig, LookoutConfig, MqttConfig


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any, bool]] = []

    def publish(self, topic: str, payload: Any = None, retain: bool = False, **kwargs: Any) -> None:
        self.published.append((topic, payload, bool(retain)))


class FakeHttp:
    """Minimal HTTP client stub so REST cleanup never touches the network."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, url: str, **kwargs: Any) -> Response:
        self.deleted.append(url)
        return Response(200, json={})


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
    http = FakeHttp()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt, http_client=http
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


def test_base_url_autodetect_from_home_assistant_url() -> None:
    """TR-12: default HA base_url falls through to top-level home_assistant_url."""
    config = LookoutConfig(home_assistant_url="http://ha.example:8123")
    manager = HomeAssistantSyncManager(config=config)
    assert manager.base_url == "http://ha.example:8123"


def test_mqtt_defaults_from_mqtt_config() -> None:
    """TR-13: HA MQTT overrides fall through to the existing mqtt config block."""
    config = LookoutConfig(mqtt=MqttConfig(host="mqtt.example", port=1884))
    manager = HomeAssistantSyncManager(config=config)
    assert manager.mqtt_host == "mqtt.example"
    assert manager.mqtt_port == 1884


@pytest.mark.asyncio
@respx.mock
async def test_mqtt_only_mode_status_and_test_connection() -> None:
    """TR-14: without a token, status is mqtt-only and no REST call is made."""
    config = LookoutConfig(home_assistant_url="http://ha.example:8123")
    manager = HomeAssistantSyncManager(config=config)
    assert manager.get_status()["mode"] == "mqtt-only"

    route = respx.get("http://ha.example:8123/api/").mock(return_value=Response(401))
    result = await manager.test_connection()
    assert result["ok"] is True
    assert result["mode"] == "mqtt-only"
    assert "no HA credentials" in result["message"]
    assert "401" not in str(result)
    assert not route.called
    assert manager.get_status().get("last_error") is None


@pytest.mark.asyncio
async def test_discovery_payloads_include_topics_and_initial_states() -> None:
    """Every discovery payload advertises a state topic; benign states follow."""
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    result = await manager.sync_camera(camera)
    assert result["ok"] is True

    configs = [json.loads(p) for (t, p, _) in mqtt.published if t.endswith("/config")]
    assert configs
    assert all("state_topic" in cfg for cfg in configs)

    camera_cfg = next(
        json.loads(p) for (t, p, _) in mqtt.published if t == "homeassistant/camera/cam1/config"
    )
    # The MQTT camera needs an image topic in addition to its state topic.
    assert camera_cfg.get("topic")
    assert camera_cfg["state_topic"]

    # binary_sensor, both sensors and the event entity carry value templates.
    templated = [cfg for cfg in configs if cfg.get("value_template")]
    assert len(templated) >= 4
    assert all("{{" in cfg["value_template"] for cfg in templated)

    # A benign JSON state payload is published for each advertised state topic.
    state_topics = {cfg["state_topic"] for cfg in configs}
    published_states = {t: json.loads(p) for (t, p, _) in mqtt.published if t in state_topics}
    assert set(published_states) == state_topics
    assert all(isinstance(payload, dict) for payload in published_states.values())


@pytest.mark.asyncio
async def test_event_deep_link_is_concrete_and_placeholder_free() -> None:
    """Event discovery/state use a real ingress URL, never ``{event_id}``."""
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    ingress = "http://ha:8123/api/hassio_ingress/abc"
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123",
        access_token="token",
        mqtt_client=mqtt,
        ingress_url=ingress,
    )
    await manager.sync_camera(camera)

    all_payloads = "\n".join(str(p) for (_, p, _) in mqtt.published)
    assert "{event_id}" not in all_payloads

    event_cfg = next(
        json.loads(p)
        for (t, p, _) in mqtt.published
        if t == "homeassistant/event/cam1_event/config"
    )
    assert event_cfg["link"] == f"{ingress}/events"
    assert event_cfg["url"] == f"{ingress}/events"
    assert event_cfg["configuration_url"] == f"{ingress}/events"
    assert event_cfg["value_template"] == "{{ value_json.link }}"

    event_state = next(
        json.loads(p) for (t, p, _) in mqtt.published if t == "lookout/cam1/event/state"
    )
    assert event_state["link"] == f"{ingress}/events"
    assert event_state["url"] == f"{ingress}/events"


@pytest.mark.asyncio
@respx.mock
async def test_remove_camera_rest_cleanup_is_best_effort() -> None:
    """AC4: REST mode attempts device registry deletion without breaking removal."""
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    await manager.sync_camera(camera)
    delete_route = respx.delete("http://ha:8123/api/devices/lookout_cam1").mock(
        return_value=Response(200, json={})
    )
    result = await manager.remove_camera("cam1")
    assert result["ok"] is True
    assert delete_route.called


@pytest.mark.asyncio
@respx.mock
async def test_remove_camera_rest_failure_does_not_break_removal() -> None:
    """A failing HA REST cleanup is swallowed; discovery cleanup still runs."""
    mqtt = FakeMqtt()
    camera = CameraConfig(id="cam1", name="Porch Cam", stream_url="rtsp://example/cam")
    manager = HomeAssistantSyncManager(
        base_url="http://ha:8123", access_token="token", mqtt_client=mqtt
    )
    await manager.sync_camera(camera)
    respx.delete("http://ha:8123/api/devices/lookout_cam1").mock(return_value=Response(500))
    result = await manager.remove_camera("cam1")
    assert result["ok"] is True
    # The MQTT retained-config clearing still happened.
    assert any(payload in ("", None, b"") for (_, payload, _) in mqtt.published)
