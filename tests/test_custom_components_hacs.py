"""Unit tests for Home Assistant custom component entities, config flow, and HACS metadata."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.lookout.binary_sensor import (
    LookoutEntityBinarySensor,
    LookoutMotionBinarySensor,
)
from custom_components.lookout.camera import LookoutCameraEntity
from custom_components.lookout.coordinator import LookoutDataUpdateCoordinator
from custom_components.lookout.select import LookoutAiModeSelect
from custom_components.lookout.sensor import (
    LookoutLatencySensor,
    LookoutThreatLevelSensor,
)


def test_hacs_manifest_and_metadata_validity() -> None:
    hacs_path = Path("hacs.json")
    assert hacs_path.is_file(), "hacs.json missing"
    hacs_data = json.loads(hacs_path.read_text(encoding="utf-8"))

    manifest_path = Path("custom_components/lookout/manifest.json")
    assert manifest_path.is_file(), "custom_components/lookout/manifest.json missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert hacs_data["name"] == "Lookout Camera Intelligence" and manifest["domain"] == "lookout"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.3.0"
    assert "httpx>=0.27.0" in manifest["requirements"]
    assert "voluptuous>=0.11.7" in manifest["requirements"]


def test_ha_custom_component_entity_discovery() -> None:
    hass = MagicMock()
    coordinator = LookoutDataUpdateCoordinator(hass, "localhost", 8000)
    coordinator.data = {
        "cameras": [
            {
                "id": "front_door",
                "name": "Front Door",
                "stream_url": "rtsp://localhost:8554/front_door",
                "enabled": True,
            }
        ]
    }

    camera_entity = LookoutCameraEntity(coordinator, coordinator.data["cameras"][0])
    motion_entity = LookoutMotionBinarySensor(coordinator, coordinator.data["cameras"][0])
    person_entity = LookoutEntityBinarySensor(coordinator, coordinator.data["cameras"][0], "person")
    threat_sensor = LookoutThreatLevelSensor(coordinator, coordinator.data["cameras"][0])
    latency_sensor = LookoutLatencySensor(coordinator, coordinator.data["cameras"][0])

    entities = [
        camera_entity.entity_id,
        motion_entity.entity_id,
        person_entity.entity_id,
        threat_sensor.entity_id,
        latency_sensor.entity_id,
    ]

    assert "camera.front_door" in entities and "binary_sensor.front_door_motion" in entities

    coordinator.data["events"] = [
        {
            "camera_id": "front_door",
            "type": "motion",
            "threat_level": "high",
            "latency_ms": 42.0,
            "labels": ["person"],
        }
    ]
    coordinator.data["cameras"][0]["enabled"] = False

    camera_entity._handle_coordinator_update()
    motion_entity._handle_coordinator_update()
    person_entity._handle_coordinator_update()
    threat_sensor._handle_coordinator_update()
    latency_sensor._handle_coordinator_update()

    assert camera_entity._attr_is_streaming is False
    assert motion_entity.is_on is True
    assert person_entity.is_on is True
    assert threat_sensor.native_value == "high"
    assert latency_sensor.native_value == 42.0


@pytest.mark.asyncio
async def test_ha_select_ai_mode_control() -> None:
    hass = MagicMock()
    coordinator = LookoutDataUpdateCoordinator(hass, "localhost", 8000)
    coordinator.data = {"ai_mode": "auto"}

    select_entity = LookoutAiModeSelect(coordinator)
    assert select_entity.current_option in {"auto", "local_only", "gemini_only", "off"}
    assert "auto" in select_entity.options
    assert "local_only" in select_entity.options
    assert "gemini_only" in select_entity.options
    assert "off" in select_entity.options

    coordinator.data["ai_mode"] = "local_only"
    select_entity._handle_coordinator_update()
    assert select_entity.current_option == "local_only"


@pytest.mark.asyncio
async def test_ha_config_flow_unique_id() -> None:
    from custom_components.lookout.config_flow import LookoutConfigFlow

    flow = LookoutConfigFlow()
    result = await flow.async_step_user({"host": "192.168.1.100", "port": 8000})
    assert result["type"] == "create_entry"
    assert result["title"] == "Lookout (192.168.1.100:8000)"
    assert result["data"]["host"] == "192.168.1.100"


@pytest.mark.asyncio
async def test_ha_setup_entry_first_refresh() -> None:
    from custom_components.lookout import async_setup_entry

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"host": "localhost", "port": 8000}

    success = await async_setup_entry(hass, entry)
    assert success is True
    coordinator = hass.data["lookout"]["test_entry"]["coordinator"]
    assert coordinator is not None
    assert "cameras" in coordinator.data


@pytest.mark.asyncio
async def test_ha_camera_image_with_clientsession() -> None:
    hass = MagicMock()
    coordinator = LookoutDataUpdateCoordinator(hass, "localhost", 8000)
    camera_data = {"id": "driveway", "name": "Driveway", "enabled": True}
    cam = LookoutCameraEntity(coordinator, camera_data)
    cam.hass = hass

    fake_response = AsyncMock()
    fake_response.status = 200
    fake_response.read.return_value = b"fake_jpeg_bytes"

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = fake_response

    with patch(
        "custom_components.lookout.camera.async_get_clientsession",
        return_value=mock_session,
    ):
        img = await cam.async_camera_image()
        assert img == b"fake_jpeg_bytes"
        mock_session.get.assert_called_once()
