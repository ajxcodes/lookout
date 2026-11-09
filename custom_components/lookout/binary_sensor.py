"""Binary sensors for Lookout motion detection, entity classification, and anomalies."""

from __future__ import annotations

import logging
from typing import Any

try:
    from homeassistant.components.binary_sensor import (
        BinarySensorDeviceClass,
        BinarySensorEntity,
    )
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:

    class BinarySensorDeviceClass:
        MOTION = "motion"
        OCCUPANCY = "occupancy"

    class BinarySensorEntity:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.entity_id = ""
            self._attr_unique_id = ""
            self._attr_name = ""
            self._attr_is_on = False
            self._attr_device_class = None

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
    """Set up Lookout binary sensors from a config entry."""
    coordinator: LookoutDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    cameras = coordinator.data.get("cameras", [])

    entities: list[BinarySensorEntity] = []
    for cam in cameras:
        # 1. Primary motion detection binary sensor
        entities.append(LookoutMotionBinarySensor(coordinator, cam))
        # 2. Entity presence detection binary sensors
        entities.append(LookoutEntityBinarySensor(coordinator, cam, "person"))
        entities.append(LookoutEntityBinarySensor(coordinator, cam, "vehicle"))
        entities.append(LookoutEntityBinarySensor(coordinator, cam, "pet"))

    async_add_entities(entities)


class LookoutMotionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating real-time motion detection for a camera."""

    def __init__(
        self, coordinator: LookoutDataUpdateCoordinator, camera_data: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self.camera_id = camera_data.get("id", "camera")
        self.entity_id = f"binary_sensor.{self.camera_id}_motion"
        self._attr_unique_id = f"{DOMAIN}_{self.camera_id}_motion"
        self._attr_name = f"{camera_data.get('name', self.camera_id)} Motion"
        self._attr_device_class = BinarySensorDeviceClass.MOTION
        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        is_motion = False
        for event in self.coordinator.data.get("events", []):
            if event.get("camera_id") == self.camera_id and (
                event.get("in_progress", False) or event.get("type") in ("motion", "detection")
            ):
                is_motion = True
                break
        self._is_on = is_motion
        self.async_write_ha_state()


class LookoutEntityBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating detected entity presence (person, vehicle, pet)."""

    def __init__(
        self,
        coordinator: LookoutDataUpdateCoordinator,
        camera_data: dict[str, Any],
        entity_type: str,
    ) -> None:
        super().__init__(coordinator)
        self.camera_id = camera_data.get("id", "camera")
        self.entity_type = entity_type
        self.entity_id = f"binary_sensor.{self.camera_id}_{entity_type}"
        self._attr_unique_id = f"{DOMAIN}_{self.camera_id}_{entity_type}"
        self._attr_name = f"{camera_data.get('name', self.camera_id)} {entity_type.capitalize()}"
        self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        detected = False
        for event in self.coordinator.data.get("events", []):
            if event.get("camera_id") == self.camera_id:
                detections = event.get("detections", [])
                labels = event.get("labels", [])
                if self.entity_type in labels or any(
                    d.get("label") == self.entity_type or d.get("category") == self.entity_type
                    for d in detections
                ):
                    detected = True
                    break
        self._is_on = detected
        self.async_write_ha_state()
