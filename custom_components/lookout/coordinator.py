"""DataUpdateCoordinator for Lookout camera feeds and event telemetry."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx

try:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
except ImportError:

    class UpdateFailed(Exception):
        """Dummy exception when homeassistant is not installed."""

    class DataUpdateCoordinator:  # type: ignore[no-redef]
        """Lightweight standalone mock coordinator for unit tests."""

        def __init__(
            self,
            hass: Any,
            logger: logging.Logger,
            name: str,
            update_interval: timedelta | None = None,
        ) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: dict[str, Any] = {}
            self._listeners: list[Any] = []

        def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
            self._listeners.append(update_callback)
            return lambda: self._listeners.remove(update_callback)

        def async_update_listeners(self) -> None:
            for listener in list(self._listeners):
                listener()

        async def async_refresh(self) -> None:
            await self._async_update_data()
            self.async_update_listeners()

        async def async_config_entry_first_refresh(self) -> None:
            await self._async_update_data()
            self.async_update_listeners()

        async def _async_update_data(self) -> dict[str, Any]:
            return self.data


from .const import DOMAIN

logger = logging.getLogger(__name__)


class LookoutDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinates polling of cameras, AI modes, and event state from Lookout REST API."""

    def __init__(self, hass: Any, host: str, port: int, api_key: str | None = None) -> None:
        super().__init__(
            hass,
            logger,
            name=f"{DOMAIN}_{host}_{port}",
            update_interval=timedelta(seconds=10),
        )
        self.host = host
        self.port = port
        self.api_key = api_key
        self.base_url = f"http://{host}:{port}"
        self.data: dict[str, Any] = {
            "cameras": [],
            "events": [],
            "ai_mode": "auto",
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest camera list and system state from Lookout."""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/api/cameras", headers=headers)
                if res.status_code == 200:
                    self.data["cameras"] = res.json().get("cameras", [])

                res_settings = await client.get(f"{self.base_url}/api/settings", headers=headers)
                if res_settings.status_code == 200:
                    self.data["ai_mode"] = res_settings.json().get("ai_mode", "auto")

            return self.data
        except Exception as err:
            logger.debug("Failed updating Lookout coordinator data: %s", err)
            return self.data
