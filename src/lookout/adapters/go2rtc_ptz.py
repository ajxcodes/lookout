"""go2rtc PTZ (pan/tilt/zoom) control adapter for Wyze bridge and ONVIF paths."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import Go2RtcConfig
from lookout.ports.egress import PtzControlPort

logger = logging.getLogger(__name__)

WYZE_PATH = "/api/cameras/{camera_id}/ptz"

ONVIF_CANDIDATES = [
    "/api/onvif/ptz?src={camera_id}",
    "/api/ptz?src={camera_id}",
    "/api/onvif?src={camera_id}",
    "/api/cameras/{camera_id}/ptz",
]


def _map_speed_wyze(speed: float) -> int:
    speed = max(0.0, min(1.0, speed))
    return int(round(speed * 8)) + 1


class Go2RtcPtzAdapter(PtzControlPort):
    """PTZ control adapter implementing Wyze bridge and go2rtc ONVIF proxy paths."""

    def __init__(
        self,
        config: Go2RtcConfig | None = None,
        bridge_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        protocol: str | None = None,
    ) -> None:
        self.config = config or Go2RtcConfig()
        self.bridge_url = (bridge_url or "").rstrip("/") if bridge_url else None
        self._external_client = client
        self.protocol = protocol
        self.last_speed: float | None = None
        self.temp_manager = TempMediaManager()

    @property
    def go2rtc_base(self) -> str:
        return self.config.api_url.rstrip("/")

    @property
    def wyze_base(self) -> str:
        if self.bridge_url:
            return self.bridge_url
        # Allow config to carry an external bridge url
        return self.go2rtc_base

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._external_client is not None:
            yield self._external_client
        else:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                yield client

    def _record_speed(self, speed: float) -> None:
        self.last_speed = speed

    async def _wyze_request(
        self, method: str, camera_id: str, payload: dict[str, Any] | None = None
    ) -> httpx.Response:
        url = f"{self.wyze_base}{WYZE_PATH.format(camera_id=camera_id)}"
        async with self._get_client() as client:
            if method.upper() == "GET":
                response = await client.get(url, params=payload or {})
            else:
                response = await client.request(method.upper(), url, json=payload)
            response.raise_for_status()
            return response

    async def _onvif_request(self, camera_id: str, payload: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for cand in ONVIF_CANDIDATES:
            path = cand.format(camera_id=camera_id)
            url = f"{self.go2rtc_base}{path}"
            try:
                async with self._get_client() as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise httpx.HTTPError("ONVIF request failed")

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> Any:
        self._record_speed(speed)
        # Wyze path first
        try:
            payload = {
                "action": "move",
                "pan": pan,
                "tilt": tilt,
                "zoom": zoom,
                "speed": _map_speed_wyze(speed),
            }
            return await self._wyze_request("POST", camera_id, payload)
        except Exception as exc:  # noqa: BLE001
            # If Wyze endpoint is genuinely unsupported, propagate
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            # Otherwise try ONVIF
            payload = {
                "action": "move",
                "pan": pan,
                "tilt": tilt,
                "zoom": zoom,
                "speed": max(0.0, min(1.0, speed)),
            }
            return await self._onvif_request(camera_id, payload)

    async def stop(self, camera_id: str, pan_tilt: bool = True, zoom: bool = True) -> Any:
        # Wyze
        try:
            payload = {"action": "stop", "pan_tilt": pan_tilt, "zoom": zoom}
            return await self._wyze_request("POST", camera_id, payload)
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {"action": "stop", "pan_tilt": pan_tilt, "zoom": zoom}
            return await self._onvif_request(camera_id, payload)

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> Any:
        self._record_speed(speed)
        try:
            payload = {
                "action": "step",
                "direction": direction,
                "amount": amount,
                "speed": _map_speed_wyze(speed),
            }
            return await self._wyze_request("POST", camera_id, payload)
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {
                "action": "step",
                "direction": direction,
                "amount": amount,
                "speed": max(0.0, min(1.0, speed)),
            }
            return await self._onvif_request(camera_id, payload)

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> Any:
        self._record_speed(speed)
        try:
            payload = {"action": "goto_preset", "preset": preset, "speed": _map_speed_wyze(speed)}
            return await self._wyze_request("POST", camera_id, payload)
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {
                "action": "goto_preset",
                "preset": preset,
                "speed": max(0.0, min(1.0, speed)),
            }
            return await self._onvif_request(camera_id, payload)

    async def set_preset(self, camera_id: str, name: str) -> Any:
        try:
            payload = {"action": "set_preset", "name": name}
            return await self._wyze_request("POST", camera_id, payload)
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {"action": "set_preset", "name": name}
            return await self._onvif_request(camera_id, payload)

    async def list_presets(self, camera_id: str) -> Any:
        try:
            return await self._wyze_request("GET", camera_id, {})
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {"action": "list_presets"}
            return await self._onvif_request(camera_id, payload)

    async def get_position(self, camera_id: str) -> Any:
        try:
            return await self._wyze_request("GET", camera_id, {"action": "position"})
        except Exception as exc:  # noqa: BLE001
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response is not None
                and exc.response.status_code in (501, 502)
            ):
                raise
            payload = {"action": "get_position"}
            return await self._onvif_request(camera_id, payload)
