"""The Lookout Camera Intelligence Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Any

import httpx

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
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    base_url = f"http://{host}:{port}"

    async def _call(path: str, payload: dict | None = None, method: str = "POST") -> None:
        url = f"{base_url}/api{path}"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "POST":
                await client.post(url, json=payload or {}, headers=headers)
            else:
                await client.get(url, headers=headers)

    async def handle_ptz_move(call: Any) -> None:
        data = dict(call.data)
        await _call(
            f"/cameras/{data.get('camera_id')}/ptz",
            {
                "action": "move",
                "pan": data.get("pan", 0.0),
                "tilt": data.get("tilt", 0.0),
                "zoom": data.get("zoom", 0.0),
                "speed": data.get("speed", 0.5),
            },
        )

    async def handle_ptz_stop(call: Any) -> None:
        data = dict(call.data)
        await _call(f"/cameras/{data.get('camera_id')}/ptz", {"action": "stop"})

    async def handle_ptz_goto_preset(call: Any) -> None:
        data = dict(call.data)
        await _call(
            f"/cameras/{data.get('camera_id')}/ptz",
            {
                "action": "goto_preset",
                "preset": data.get("preset"),
                "speed": data.get("speed", 0.5),
            },
        )

    async def handle_capture_snapshot(call: Any) -> None:
        data = dict(call.data)
        await _call(f"/cameras/{data.get('camera_id')}/snapshot", {})

    async def handle_record_clip(call: Any) -> None:
        data = dict(call.data)
        await _call(
            f"/cameras/{data.get('camera_id')}/record",
            {"duration": data.get("duration", 10.0)},
        )

    for name, handler in (
        ("ptz_move", handle_ptz_move),
        ("ptz_stop", handle_ptz_stop),
        ("ptz_goto_preset", handle_ptz_goto_preset),
        ("capture_snapshot", handle_capture_snapshot),
        ("record_clip", handle_record_clip),
    ):
        hass.services.async_register(DOMAIN, name, handler)

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
