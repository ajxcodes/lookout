"""MQTT-based PTZ (pan/tilt) control adapter for Wyze pan cameras via wyze-bridge."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from lookout.config.schema import MqttConfig
from lookout.ports.egress import PtzControlPort

logger = logging.getLogger(__name__)


def _mqtt_exception(status_code: int, detail: str) -> Exception:
    try:
        fastapi = __import__("fastapi")
        return fastapi.HTTPException(status_code=status_code, detail=detail)  # type: ignore[no-any-return]
    except Exception:  # pragma: no cover
        exc = Exception(detail)
        exc.status_code = status_code  # type: ignore[attr-defined]
        return exc


class MqttWyzePtzAdapter(PtzControlPort):
    """PTZ control adapter publishing commands to wyze-bridge over MQTT."""

    def __init__(
        self,
        config: MqttConfig | None = None,
        client: Any | None = None,
        topic: str | None = None,
        cam_name_map: dict[str, str] | None = None,
    ) -> None:
        self.config = config or MqttConfig()
        self._external_client = client
        self.topic = (topic or self.config.wyze_topic or "wyzebridge").strip("/")
        self.cam_name_map = cam_name_map or {}
        self.last_speed: float | None = None
        self._client: Any | None = None

    def _bridge_name(self, camera_id: str) -> str:
        return self.cam_name_map.get(camera_id, camera_id)

    @property
    def client(self) -> Any:
        if self._external_client is not None:
            return self._external_client
        if self._client is None:
            try:
                import paho.mqtt.client as mqtt

                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # type: ignore[attr-defined]
                if self.config.username:
                    c.username_pw_set(self.config.username, self.config.password)
                c.connect(self.config.host, int(self.config.port or 1883), 10)
                c.loop_start()
                self._client = c
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("MQTT connect failed: %s", exc)
                self._client = None
        return self._client

    def _publish(self, camera_id: str, command: str, payload: Any) -> None:
        c = self.client
        if c is None:
            raise _mqtt_exception(502, "MQTT broker unavailable")
        topic = f"{self.topic}/{self._bridge_name(camera_id)}/{command}/set"
        c.publish(topic, payload)
        logger.debug("MQTT publish %s = %r", topic, payload)

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    def _record_speed(self, speed: float) -> None:
        self.last_speed = speed

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> Any:
        self._record_speed(speed)
        # Map pan/tilt signs to wyze rotary_degree payloads
        if pan and pan > 0:
            await self._run(self._publish, camera_id, "rotary_degree", "right")
        elif pan and pan < 0:
            await self._run(self._publish, camera_id, "rotary_degree", "left")
        if tilt and tilt > 0:
            await self._run(self._publish, camera_id, "rotary_degree", "up")
        elif tilt and tilt < 0:
            await self._run(self._publish, camera_id, "rotary_degree", "down")
        return {"status": "ok"}

    async def stop(self, camera_id: str, pan_tilt: bool = True, zoom: bool = True) -> Any:
        # wyze-bridge has no explicit stop; reset rotation to home
        with contextlib.suppress(Exception):
            await self._run(self._publish, camera_id, "reset_rotation", "")
        return {"status": "ok"}

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> Any:
        self._record_speed(speed)
        direction = (direction or "").lower()
        mapping = {
            "left": "left",
            "right": "right",
            "up": "up",
            "down": "down",
            "pan_left": "left",
            "pan_right": "right",
            "tilt_up": "up",
            "tilt_down": "down",
        }
        if direction in mapping:
            await self._run(self._publish, camera_id, "rotary_degree", mapping[direction])
            return {"status": "ok"}
        if direction in ("zoom_in", "zoom_out"):
            raise _mqtt_exception(501, "zoom not supported by wyze pan cameras")
        raise _mqtt_exception(400, f"Unknown direction: {direction}")

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> Any:
        self._record_speed(speed)
        if not str(preset).isdigit():
            raise _mqtt_exception(400, "preset must be a numeric cruise point")
        await self._run(self._publish, camera_id, "cruise_point", str(preset))
        return {"status": "ok"}

    async def set_preset(self, camera_id: str, name: str) -> Any:
        raise _mqtt_exception(501, "set_preset not supported via MQTT")

    async def list_presets(self, camera_id: str) -> Any:
        raise _mqtt_exception(501, "list_presets not supported via MQTT")

    async def get_position(self, camera_id: str) -> Any:
        raise _mqtt_exception(501, "get_position not supported via MQTT")
