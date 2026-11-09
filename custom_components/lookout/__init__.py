"""The Lookout Camera Intelligence Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Any

from .const import CONF_API_KEY, CONF_HOST, CONF_PORT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN, PLATFORMS
from .coordinator import LookoutDataUpdateCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up Lookout from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    api_key = entry.data.get(CONF_API_KEY)

    coordinator = LookoutDataUpdateCoordinator(hass, host, port, api_key)
    await coordinator._async_update_data()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    if hasattr(hass.config_entries, "async_forward_entry_setups"):
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a Lookout config entry."""
    if hasattr(hass.config_entries, "async_unload_platforms"):
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    else:
        unload_ok = True

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
