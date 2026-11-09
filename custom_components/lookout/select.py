"""Select entity for controlling Lookout runtime AI mode from Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import httpx

try:
    from homeassistant.components.select import SelectEntity
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:

    class SelectEntity:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.entity_id = ""
            self._attr_unique_id = ""
            self._attr_name = ""
            self._attr_options: list[str] = []
            self._attr_current_option: str | None = None

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

AI_MODES = ["auto", "local_only", "gemini_only", "off"]


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lookout select entities from a config entry."""
    coordinator: LookoutDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([LookoutAiModeSelect(coordinator)])


class LookoutAiModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity controlling active AI vision mode."""

    def __init__(self, coordinator: LookoutDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self.entity_id = "select.lookout_ai_mode"
        self._attr_unique_id = f"{DOMAIN}_ai_mode"
        self._attr_name = "Lookout AI Mode"
        self._attr_options = AI_MODES
        self._attr_current_option = coordinator.data.get("ai_mode", "auto")

    @property
    def current_option(self) -> str:
        """Return the current selected AI mode."""
        return self._attr_current_option or "auto"

    @property
    def options(self) -> list[str]:
        """Return the list of available AI mode options."""
        return self._attr_options

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        mode = self.coordinator.data.get("ai_mode")
        if mode and mode in self._attr_options:
            self._attr_current_option = mode
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected AI mode via Lookout REST API."""
        if option not in self._attr_options:
            raise ValueError(f"Invalid option '{option}'. Expected one of {self._attr_options}")

        url = f"{self.coordinator.base_url}/api/settings/ai-mode"
        headers = {}
        if self.coordinator.api_key:
            headers["Authorization"] = f"Bearer {self.coordinator.api_key}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json={"mode": option}, headers=headers)
                if res.status_code == 200:
                    self._attr_current_option = option
                    self.coordinator.data["ai_mode"] = option
                    self.async_write_ha_state()
        except Exception as e:
            logger.error("Failed updating Lookout AI mode to '%s': %s", option, e)
