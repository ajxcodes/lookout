"""Native Wyze ingestion adapter (cloud API + TUTK).

Implements ``VideoRecorderPort`` and ``PtzControlPort`` using the Wyze cloud API
for control and an embedded go2rtc instance for TUTK media.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lookout.adapters.native_stream_manager import MediaCaptureError, NativeStreamManager
from lookout.config.schema import WyzeConfig
from lookout.ports.egress import PtzControlPort, VideoRecorderPort

logger = logging.getLogger(__name__)

_JPEG_MAGIC = b"\xff\xd8"
_MP4_FTYP = b"ftyp"
_PTZ_SPEED_MIN = 1
_PTZ_SPEED_MAX = 9


class WyzeAuthError(RuntimeError):
    """Raised when no usable Wyze credentials are configured."""


class WyzeApiError(RuntimeError):
    """Raised when a Wyze cloud API call fails or returns a non-success code."""


class WyzeStreamSourceError(RuntimeError):
    """Raised when a Wyze device lacks the fields required to build a go2rtc source."""


def _as_utc(value: Any) -> datetime:
    """Normalize a datetime/str/epoch value to a timezone-aware UTC datetime."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        if abs(number) >= 1e12:
            number = number / 1000.0
        return datetime.fromtimestamp(number, tz=UTC)
    if isinstance(value, str):
        stripped = value.strip()
        # Try ISO-8601 first.
        try:
            return _as_utc(datetime.fromisoformat(stripped.replace("Z", "+00:00")))
        except ValueError:
            pass
        # Then try a numeric (epoch) string.
        try:
            return _as_utc(float(stripped))
        except ValueError:
            pass
    raise ValueError(f"Cannot normalize value to UTC datetime: {value!r}")


class WyzeNativeAdapter(VideoRecorderPort, PtzControlPort):
    """Native Wyze adapter using Wyze cloud APIs and go2rtc TUTK streaming."""

    def __init__(
        self,
        config: WyzeConfig | None = None,
        temp_manager: Any | None = None,
        stream_manager: NativeStreamManager | None = None,
        client: Any | None = None,
        cameras: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.config = config or WyzeConfig()
        self.temp_manager = temp_manager
        self.stream_manager = stream_manager
        self.cameras = list(cameras) if cameras is not None else []
        self._client_instance = client
        self._devices: dict[str, dict[str, Any]] = {}
        self._ptz = WyzeTutkPtzAdapter(
            config=self.config, client=self, stream_manager=stream_manager
        )

    # ------------------------------------------------------------------
    # Compatibility PTZ surface (delegates to the TUTK/cloud PTZ adapter)
    # ------------------------------------------------------------------
    @property
    def last_command(self) -> str | None:
        return self._ptz.last_command

    @property
    def last_speed(self) -> float | None:
        return self._ptz.last_speed

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> Any:
        return await self._ptz.move(camera_id, pan, tilt, zoom, speed)

    async def stop(self, camera_id: str, pan_tilt: bool = True, zoom: bool = True) -> Any:
        return await self._ptz.stop(camera_id, pan_tilt=pan_tilt, zoom=zoom)

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> Any:
        return await self._ptz.step(camera_id, direction, amount=amount, speed=speed)

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> Any:
        return await self._ptz.goto_preset(camera_id, preset, speed=speed)

    async def set_preset(self, camera_id: str, name: str) -> Any:
        return await self._ptz.set_preset(camera_id, name)

    async def list_presets(self, camera_id: str) -> Any:
        return await self._ptz.list_presets(camera_id)

    async def get_position(self, camera_id: str) -> Any:
        return await self._ptz.get_position(camera_id)

    # ------------------------------------------------------------------
    # Wyze cloud client
    # ------------------------------------------------------------------
    def _build_client(self) -> Any:
        cfg = self.config
        token = getattr(cfg, "auth_token", None)
        refresh_token = getattr(cfg, "refresh_token", None)
        email = getattr(cfg, "email", None)
        password = getattr(cfg, "password", None)
        key_id = getattr(cfg, "key_id", None)
        api_key = getattr(cfg, "api_key", None)
        totp_key = getattr(cfg, "totp_key", None)

        try:
            # wyze-sdk ships no py.typed marker; its public surface is untyped.
            from wyze_sdk import Client  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise WyzeAuthError(
                "wyze-sdk is not installed; install 'wyze-sdk>=2.3.16' to use native Wyze ingestion"
            ) from exc

        if token or refresh_token:
            return Client(
                token=token,
                refresh_token=refresh_token,
                timeout=int(getattr(cfg, "timeout_seconds", 30) or 30),
            )
        if email and password and key_id and api_key:
            return Client(
                email=email,
                password=password,
                key_id=key_id,
                api_key=api_key,
                totp_key=totp_key,
                timeout=int(getattr(cfg, "timeout_seconds", 30) or 30),
            )
        raise WyzeAuthError(
            "Wyze credentials are not configured. Set wyze.auth_token/refresh_token "
            "or wyze.email/password/key_id/api_key."
        )

    def _client(self) -> Any:
        if self._client_instance is None:
            self._client_instance = self._build_client()
        return self._client_instance

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking wyze_sdk call in a worker thread."""
        return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Discovery / connection
    # ------------------------------------------------------------------
    def _raw_cameras(self) -> list[dict[str, Any]]:
        client = self._client()
        cameras_client = getattr(client, "cameras", None)
        if cameras_client is None or not hasattr(cameras_client, "_list_cameras"):
            raise WyzeApiError("Wyze client does not expose camera listing")
        raw = cameras_client._list_cameras()
        return [dict(item) for item in raw if isinstance(item, dict)]

    async def test_connection(self) -> dict[str, Any]:
        """Validate credentials against the Wyze cloud and list cameras."""
        try:
            raw_cameras = await self._run_sync(self._raw_cameras)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.info("Wyze connection test failed: %s", exc)
            return {
                "reachable": False,
                "provider": "wyze",
                "devices": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        devices = [self._map_raw_camera(item) for item in raw_cameras]
        return {
            "reachable": True,
            "provider": "wyze",
            "devices": devices,
            "error": None,
        }

    def _map_raw_camera(self, raw: dict[str, Any]) -> dict[str, Any]:
        mac = raw.get("mac")
        model = raw.get("product_model") or raw.get("model")
        # Wyze nests network/TUTK details under ``device_params`` for most models.
        params = raw.get("device_params")
        if not isinstance(params, dict):
            params = {}
        ip = raw.get("ip") or params.get("ip")
        p2p_id = raw.get("p2p_id") or params.get("p2p_id")
        device: dict[str, Any] = {
            "id": str(mac) if mac else "",
            "name": raw.get("nickname") or (str(mac) if mac else "Wyze Camera"),
            "mac": str(mac) if mac else None,
            "model": model,
            "ip": ip,
            "p2p_id": p2p_id,
            "enr": raw.get("enr"),
            "dtls": raw.get("dtls", params.get("dtls")),
            "native_provider": "wyze",
            "stream_source": None,
        }
        try:
            device["stream_source"] = self.build_stream_source(device)
        except WyzeStreamSourceError as exc:
            logger.warning("Skipping stream source for Wyze camera %s: %s", mac, exc)
        return device

    async def discover_devices(self) -> list[dict[str, Any]]:
        """Discover Wyze cameras and cache them for source resolution."""
        raw_cameras = await self._run_sync(self._raw_cameras)
        devices = [self._map_raw_camera(item) for item in raw_cameras]
        self._cache_devices(devices)
        return devices

    def _cache_devices(self, devices: list[dict[str, Any]]) -> None:
        for device in devices:
            for key in (
                device.get("mac"),
                device.get("id"),
                device.get("p2p_id"),
                device.get("ip"),
            ):
                if key:
                    self._devices[str(key)] = device

    def cache_devices(self, devices: list[dict[str, Any]]) -> None:
        """Public helper for tests and callers to prime the discovery cache."""
        self._cache_devices(devices)

    def build_stream_source(self, device: dict[str, Any]) -> str:
        """Build the exact go2rtc ``wyze://`` TUTK source URL for a device."""
        params = device.get("device_params")
        if not isinstance(params, dict):
            params = {}
        ip = device.get("ip") or params.get("ip")
        uid = device.get("p2p_id") or params.get("p2p_id")
        enr = device.get("enr")
        mac = device.get("mac")
        model = device.get("model") or device.get("product_model")
        missing = [
            name
            for name, value in (
                ("ip", ip),
                ("p2p_id", uid),
                ("enr", enr),
                ("mac", mac),
            )
            if not value
        ]
        if missing:
            raise WyzeStreamSourceError(
                f"Cannot build wyze:// source: missing {', '.join(missing)}"
            )
        return (
            f"wyze://{ip}"
            f"?uid={quote(str(uid), safe='')}"
            f"&enr={quote(str(enr), safe='')}"
            f"&mac={quote(str(mac), safe='')}"
            f"&model={quote(str(model or ''), safe='')}"
            "&subtype=hd&dtls=true"
        )

    # ------------------------------------------------------------------
    # Camera resolution
    # ------------------------------------------------------------------
    def _camera_config(self, camera_id: str) -> Any | None:
        cameras = self.cameras or getattr(self.config, "cameras", None) or []
        for camera in cameras:
            if str(getattr(camera, "id", "")) == str(camera_id):
                return camera
            if str(getattr(camera, "native_device_id", "") or "") == str(camera_id):
                return camera
        return None

    def _find_cached(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        return self._devices.get(str(key))

    def _resolve_source(self, camera_id: str) -> str:
        """Resolve a camera id to a go2rtc wyze:// source URL."""
        camera = self._camera_config(camera_id)
        if camera is not None:
            stream_url = getattr(camera, "stream_url", None)
            if stream_url and str(stream_url).startswith("wyze://"):
                return str(stream_url)
        device = self._find_cached(camera.native_device_id) if camera is not None else None
        if device is None:
            device = self._find_cached(camera_id)
        if device is not None:
            return self.build_stream_source(device)
        raise WyzeStreamSourceError(
            f"No Wyze device resolved for camera '{camera_id}'. Run native discovery first."
        )

    def _resolve_mac_model(self, camera_id: str) -> tuple[str, str]:
        camera = self._camera_config(camera_id)
        device = self._find_cached(camera.native_device_id) if camera is not None else None
        if device is None:
            device = self._find_cached(camera_id)
        mac: str | None = None
        model: str | None = None
        if device is not None:
            mac = device.get("mac")
            model = device.get("model")
        if mac is None and camera is not None:
            mac = getattr(camera, "native_device_id", None)
        if mac is None:
            mac = str(camera_id)
        if not model:
            raise WyzeStreamSourceError(f"No Wyze product model resolved for camera '{camera_id}'")
        return str(mac), str(model)

    def _resolve_mac(self, camera_id: str) -> str:
        """Resolve the Wyze MAC for event filtering without requiring a model."""
        camera = self._camera_config(camera_id)
        device = self._find_cached(camera.native_device_id) if camera is not None else None
        if device is None:
            device = self._find_cached(camera_id)
        if device is not None and device.get("mac"):
            return str(device["mac"])
        if camera is not None and getattr(camera, "native_device_id", None):
            return str(camera.native_device_id)
        return str(camera_id)

    # ------------------------------------------------------------------
    # Media capture
    # ------------------------------------------------------------------
    def _require_stream_manager(self) -> NativeStreamManager:
        if self.stream_manager is None:
            raise MediaCaptureError("No NativeStreamManager configured for native Wyze capture")
        return self.stream_manager

    def _write_temp(self, suffix: str, content: bytes) -> str:
        if self.temp_manager is not None:
            temp_path = Path(self.temp_manager.create_temp_file(suffix=suffix))
            temp_path.write_bytes(content)
            return str(temp_path)
        fd, temp_name = tempfile.mkstemp(suffix=suffix, prefix="lookout-wyze-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return temp_name

    async def capture_snapshot(self, camera_id: str) -> str:
        """Capture a real JPEG frame for a native Wyze camera."""
        camera_id = str(camera_id)
        source = self._resolve_source(camera_id)
        manager = self._require_stream_manager()
        await manager.ensure_stream(camera_id, source)
        content = await manager.frame(camera_id)
        if not content.startswith(_JPEG_MAGIC):
            raise MediaCaptureError(
                f"Snapshot for '{camera_id}' is not a JPEG ({len(content)} bytes)"
            )
        return self._write_temp(".jpg", content)

    async def record_clip(self, camera_id: str, duration_seconds: float = 10.0) -> str:
        """Record a real MP4 clip for a native Wyze camera."""
        camera_id = str(camera_id)
        source = self._resolve_source(camera_id)
        manager = self._require_stream_manager()
        await manager.ensure_stream(camera_id, source)
        content = await manager.clip(camera_id, duration_seconds)
        if _MP4_FTYP not in content[:64]:
            raise MediaCaptureError(f"Clip for '{camera_id}' is not an MP4 ({len(content)} bytes)")
        return self._write_temp(".mp4", content)

    # ------------------------------------------------------------------
    # Events / actions
    # ------------------------------------------------------------------
    async def poll_events(
        self, camera_id: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Poll Wyze cloud events for a camera, filtered to events after ``since``."""
        camera_id = str(camera_id)
        mac = self._resolve_mac(camera_id)
        begin = since if since is not None else datetime.now(UTC) - timedelta(days=1)
        end = datetime.now(UTC)

        def _fetch() -> list[Any]:
            return list(
                self._client().events.list(
                    device_ids=[mac],
                    begin=_as_utc(begin),
                    end=_as_utc(end),
                    limit=20,
                    order_by=2,
                )
            )

        events = await self._run_sync(_fetch)
        payloads: list[dict[str, Any]] = []
        for event in events:
            event_time = getattr(event, "time", None)
            if (
                event_time is not None
                and since is not None
                and _as_utc(event_time) < _as_utc(since)
            ):
                continue
            alarm_type = getattr(event, "alarm_type", None)
            alarm_description = getattr(alarm_type, "description", None)
            tags = [
                getattr(tag, "description", str(tag))
                for tag in (getattr(event, "tags", None) or [])
            ]
            payloads.append(
                {
                    "event_id": str(getattr(event, "id", "")),
                    "camera_id": camera_id,
                    "mac": mac,
                    "timestamp": _as_utc(event_time).isoformat()
                    if event_time is not None
                    else None,
                    "alarm_type": alarm_description,
                    "ai_tags": tags,
                    "metadata": {
                        "provider": "wyze",
                        "event_category": getattr(event, "category", None),
                        "parameters": getattr(event, "parameters", None),
                    },
                }
            )
        return payloads

    async def run_action(
        self, camera_id: str, action_key: str, action_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a Wyze cloud action and return the raw response payload."""
        camera_id = str(camera_id)
        mac, model = self._resolve_mac_model(camera_id)
        params = dict(action_params or {})
        client = self._client()
        api_client = getattr(getattr(client, "cameras", None), "_api_client", None)
        if api_client is None:
            raise WyzeApiError("Wyze client does not expose run_action")

        def _call() -> Any:
            return api_client().run_action(
                mac=mac,
                provider_key=model,
                action_key=action_key,
                action_params=params,
            )

        response = await self._run_sync(_call)
        data = getattr(response, "data", None)
        if not isinstance(data, dict):
            data = {}
        code = data.get("code")
        msg = data.get("msg")
        if str(code) != "1":
            raise WyzeApiError(
                f"Wyze run_action '{action_key}' failed for '{camera_id}': code={code} msg={msg}"
            )
        return {"status": "ok", "action_key": action_key, "response": data}


class WyzeTutkPtzAdapter(PtzControlPort):
    """Cloud PTZ adapter for Wyze pan/tilt cameras.

    Wyze exposes continuous movement through the ``rotary_degree`` cloud action and
    directional steps through ``rotary_action``. Zoom is not exposed by the cloud
    API, so zoom-only moves return an explicit unsupported result.
    """

    def __init__(
        self,
        config: WyzeConfig | None = None,
        client: Any | None = None,
        stream_manager: NativeStreamManager | None = None,
        **kwargs: Any,
    ) -> None:
        self.config = config
        self.client = client
        self.stream_manager = stream_manager
        self.last_command: str | None = None
        self.last_speed: float | None = None
        self._last_position: dict[str, float] = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}

    @staticmethod
    def _clamp_speed(speed: float) -> float:
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 0.5
        return max(0.0, min(1.0, value))

    @staticmethod
    def _map_speed(speed: float) -> int:
        """Map normalized speed (0..1) to Wyze's 1..9 cloud speed range."""
        normalized = max(0.0, min(1.0, float(speed)))
        return max(_PTZ_SPEED_MIN, min(_PTZ_SPEED_MAX, math.ceil(normalized * _PTZ_SPEED_MAX)))

    async def _run_action(
        self, camera_id: str, action_key: str, action_params: dict[str, Any]
    ) -> dict[str, Any]:
        client = self.client
        if client is None:
            raise WyzeApiError("No Wyze client configured for PTZ control")
        if hasattr(client, "run_action"):
            result: Any = await client.run_action(camera_id, action_key, action_params)
            return dict(result) if isinstance(result, dict) else {"response": result}
        cameras = getattr(client, "cameras", None)
        if cameras is None:
            raise WyzeApiError("Configured Wyze client does not support run_action")
        api_client = getattr(cameras, "_api_client", None)
        if api_client is None:
            raise WyzeApiError("Configured Wyze client does not support run_action")
        raw_cameras = await asyncio.to_thread(cameras._list_cameras)
        match = next(
            (
                dict(item)
                for item in raw_cameras
                if isinstance(item, dict) and str(item.get("mac")) == str(camera_id)
            ),
            None,
        )
        if match is None:
            raise WyzeApiError(f"Wyze camera '{camera_id}' was not found for PTZ control")

        def _call() -> Any:
            return api_client().run_action(
                mac=str(match["mac"]),
                provider_key=str(match["product_model"]),
                action_key=action_key,
                action_params=action_params,
            )

        response = await asyncio.to_thread(_call)
        data = getattr(response, "data", None)
        if not isinstance(data, dict):
            data = {}
        if str(data.get("code")) != "1":
            raise WyzeApiError(
                f"Wyze PTZ action '{action_key}' failed: code={data.get('code')} "
                f"msg={data.get('msg')}"
            )
        return {"status": "ok", "action_key": action_key, "response": data}

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> Any:
        self.last_command = "move"
        self.last_speed = self._clamp_speed(speed)
        pan_value = float(pan or 0.0)
        tilt_value = float(tilt or 0.0)
        zoom_value = float(zoom or 0.0)
        self._last_position = {
            "pan": pan_value,
            "tilt": tilt_value,
            "zoom": zoom_value,
        }
        if abs(pan_value) < 1e-9 and abs(tilt_value) < 1e-9:
            if abs(zoom_value) > 1e-9:
                return {
                    "status": "unsupported",
                    "reason": "zoom not exposed by Wyze cloud PTZ",
                }
            return {"status": "ok", "action_key": None, "response": None}
        result = await self._run_action(
            str(camera_id),
            "rotary_degree",
            {
                "horizontal": int(round(pan_value * 90)),
                "vertical": int(round(tilt_value * 90)),
                "speed": self._map_speed(self.last_speed or 0.5),
            },
        )
        return result

    async def stop(self, camera_id: str, pan_tilt: bool = True, zoom: bool = True) -> Any:
        self.last_command = "stop"
        if not pan_tilt and not zoom:
            return {"status": "ok", "action_key": None, "response": None}
        return await self._run_action(
            str(camera_id),
            "rotary_action",
            {"horizontal": 0, "vertical": 0, "speed": self._map_speed(0.5)},
        )

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> Any:
        self.last_command = "step"
        self.last_speed = self._clamp_speed(speed)
        normalized = str(direction).lower()
        horizontal = 0
        vertical = 0
        if normalized == "left":
            horizontal = 1
        elif normalized == "right":
            horizontal = 2
        elif normalized == "up":
            vertical = 1
        elif normalized == "down":
            vertical = 2
        elif normalized == "stop":
            horizontal = 0
            vertical = 0
        else:
            return {
                "status": "unsupported",
                "reason": f"Unknown PTZ direction '{direction}'",
            }
        return await self._run_action(
            str(camera_id),
            "rotary_action",
            {
                "horizontal": horizontal,
                "vertical": vertical,
                "speed": self._map_speed(self.last_speed or 0.5),
            },
        )

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> Any:
        self.last_command = "goto_preset"
        self.last_speed = self._clamp_speed(speed)
        try:
            point = int(str(preset))
        except (TypeError, ValueError):
            return {
                "status": "unsupported",
                "reason": "Wyze cloud PTZ only exposes numeric cruise points",
            }
        return await self._run_action(str(camera_id), "cruise_point", {"point": point})

    async def set_preset(self, camera_id: str, name: str) -> Any:
        self.last_command = "set_preset"
        return {
            "status": "unsupported",
            "reason": "Wyze cloud PTZ does not expose named presets",
        }

    async def list_presets(self, camera_id: str) -> Any:
        return {
            "status": "unsupported",
            "presets": [],
            "reason": "Wyze cloud PTZ does not expose named presets",
        }

    async def get_position(self, camera_id: str) -> Any:
        return {
            **self._last_position,
            "source": "last_command",
        }
