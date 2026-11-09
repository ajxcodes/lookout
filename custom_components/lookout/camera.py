"""Home Assistant camera entity for Lookout feeds."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

try:
    from homeassistant.components.camera import Camera, CameraEntityFeature
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:

    class CameraEntityFeature:
        STREAM = 1

    class Camera:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.hass: Any = None
            self.entity_id = ""
            self._attr_unique_id = ""
            self._attr_name = ""
            self._attr_is_streaming = True
            self._attr_supported_features = CameraEntityFeature.STREAM

    class CoordinatorEntity:  # type: ignore[no-redef]
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        def _handle_coordinator_update(self) -> None:
            pass

        def async_write_ha_state(self) -> None:
            pass

    def async_get_clientsession(hass: Any) -> Any:  # type: ignore[misc]
        return None


from .const import DOMAIN
from .coordinator import LookoutDataUpdateCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lookout camera entities from a config entry."""
    coordinator: LookoutDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    cameras = coordinator.data.get("cameras", [])

    entities = [LookoutCameraEntity(coordinator, cam) for cam in cameras]
    async_add_entities(entities)


class LookoutCameraEntity(CoordinatorEntity, Camera):
    """Represents a Lookout camera feed with WebRTC and live snapshot support."""

    def __init__(
        self, coordinator: LookoutDataUpdateCoordinator, camera_data: dict[str, Any]
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.camera_data = camera_data
        self.camera_id = camera_data.get("id", "camera")
        self.entity_id = f"camera.{self.camera_id}"
        self._attr_unique_id = f"{DOMAIN}_{self.camera_id}"
        self._attr_name = camera_data.get("name", self.camera_id)
        self._attr_is_streaming = camera_data.get("enabled", True)
        self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def is_on(self) -> bool:
        return bool(self.camera_data.get("enabled", True))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        for cam in self.coordinator.data.get("cameras", []):
            if cam.get("id") == self.camera_id:
                self.camera_data = cam
                self._attr_is_streaming = cam.get("enabled", True)
                break
        self.async_write_ha_state()

    async def stream_source(self) -> str | None:
        """Return RTSP or WebRTC stream source URL."""
        return self.camera_data.get("stream_url")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch latest camera snapshot from Lookout REST endpoint."""
        url = f"{self.coordinator.base_url}/api/cameras/{self.camera_id}/snapshot"
        try:
            hass = getattr(self, "hass", None) or getattr(self.coordinator, "hass", None)
            timeout = aiohttp.ClientTimeout(total=10.0)

            if hass is not None:
                session = async_get_clientsession(hass)
                if session is not None:
                    async with session.get(url, timeout=timeout) as res:
                        if res.status == 200:
                            return await res.read()
                    return None

            # Standalone or mock fallback without bound hass session
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url) as res,
            ):
                if res.status == 200:
                    return await res.read()
        except Exception as e:
            logger.debug("Failed fetching camera snapshot for %s: %s", self.camera_id, e)
        return None
