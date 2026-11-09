"""Unit tests for Home Assistant custom component entities, config flow, and HACS metadata."""

import json
from pathlib import Path
from unittest.mock import MagicMock

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
