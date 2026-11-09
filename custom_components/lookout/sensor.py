"""Sensor entities for Lookout threat level, confidence, and latency."""

from __future__ import annotations

import logging
from typing import Any

try:
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:

    class SensorEntity:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.entity_id = ""
            self._attr_unique_id = ""
            self._attr_name = ""
            self._attr_native_value = None

    class CoordinatorEntity:  # type: ignore[no-redef]
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        def _handle_coordinator_update(self) -> None:
            pass

        def async_write_ha_state(self) -> None:
            pass


from .const import DOMAIN
from .coordinator import LookoutDataUpdateCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lookout sensors from a config entry."""
    coordinator: LookoutDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    cameras = coordinator.data.get("cameras", [])

    entities: list[SensorEntity] = []
    for cam in cameras:
        entities.append(LookoutThreatLevelSensor(coordinator, cam))
        entities.append(LookoutLatencySensor(coordinator, cam))

    async_add_entities(entities)


class LookoutThreatLevelSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the current threat classification tier for a camera."""

    def __init__(
        self, coordinator: LookoutDataUpdateCoordinator, camera_data: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self.camera_id = camera_data.get("id", "camera")
        self.entity_id = f"sensor.{self.camera_id}_threat_level"
        self._attr_unique_id = f"{DOMAIN}_{self.camera_id}_threat_level"
        self._attr_name = f"{camera_data.get('name', self.camera_id)} Threat Level"
        self._state = "low"
        self._attr_native_value = self._state

    @property
    def native_value(self) -> str:
        return self._state

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        for event in self.coordinator.data.get("events", []):
            if event.get("camera_id") == self.camera_id and "threat_level" in event:
                self._state = event["threat_level"]
                self._attr_native_value = self._state
                break
        self.async_write_ha_state()


class LookoutLatencySensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the AI cascade inference latency in milliseconds."""

    def __init__(
        self, coordinator: LookoutDataUpdateCoordinator, camera_data: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self.camera_id = camera_data.get("id", "camera")
        self.entity_id = f"sensor.{self.camera_id}_inference_latency"
        self._attr_unique_id = f"{DOMAIN}_{self.camera_id}_inference_latency"
        self._attr_name = f"{camera_data.get('name', self.camera_id)} Inference Latency"
        self._attr_native_unit_of_measurement = "ms"
        self._state = 12.5
        self._attr_native_value = self._state

    @property
    def native_value(self) -> float:
        return self._state

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        for event in self.coordinator.data.get("events", []):
            if event.get("camera_id") == self.camera_id and "latency_ms" in event:
                self._state = float(event["latency_ms"])
                self._attr_native_value = self._state
                break
        self.async_write_ha_state()
