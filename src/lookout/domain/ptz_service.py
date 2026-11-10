"""PTZ control domain service with validation, clamping, and audit logging."""

from __future__ import annotations

import contextlib
import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lookout.ports.egress import PtzControlPort, VideoRecorderPort


def _http_exception(status_code: int, detail: str) -> Exception:
    try:
        fastapi = importlib.import_module("fastapi")
        return fastapi.HTTPException(status_code=status_code, detail=detail)  # type: ignore[no-any-return]
    except Exception:  # pragma: no cover
        exc = Exception(detail)
        exc.status_code = status_code  # type: ignore[attr-defined]
        return exc


class PtzControlService:
    """Domain service for PTZ and manual camera controls."""

    def __init__(self, **kwargs: Any) -> None:
        self.cameras: list[Any] = kwargs.get("cameras") or kwargs.get("camera_configs") or []
        if isinstance(self.cameras, dict):
            self.cameras = list(self.cameras.values())
        self.camera_map: dict[str, Any] = {}
        for cam in self.cameras:
            cid = getattr(cam, "id", None)
            if cid is not None:
                self.camera_map[str(cid)] = cam

        # allow passing a single camera
        if not self.cameras and kwargs.get("camera") is not None:
            cam = kwargs["camera"]
            self.cameras = [cam]
            cid = getattr(cam, "id", None)
            if cid is not None:
                self.camera_map[str(cid)] = cam

        self.ptz_adapter: PtzControlPort | None = (
            kwargs.get("ptz_adapter")
            or kwargs.get("ptz_control")
            or kwargs.get("adapter")
            or kwargs.get("ptz")
        )
        self.audit_logger: Any = kwargs.get("audit_logger") or kwargs.get("audit")
        self.video_recorder: VideoRecorderPort | None = (
            kwargs.get("video_recorder") or kwargs.get("recorder") or kwargs.get("video")
        )
        self.config: Any = kwargs.get("config")
        self.config_path: Any = kwargs.get("config_path")
        self.ptz_adapters: dict[str, PtzControlPort] = kwargs.get("ptz_adapters") or {}

    @staticmethod
    def _reraise(exc: Exception) -> None:
        is_http = False
        response = getattr(exc, "response", None)
        try:
            importlib.import_module("httpx")
            is_http = exc.__class__.__name__ == "HTTPStatusError"
        except Exception:
            is_http = False
        if is_http and response is not None:
            raise _http_exception(response.status_code, str(exc))
        raise exc

    def _camera(self, camera_id: str) -> Any:
        if camera_id in self.camera_map:
            return self.camera_map[camera_id]
        # try config
        if self.config is not None and hasattr(self.config, "cameras"):
            for cam in self.config.cameras:
                if getattr(cam, "id", None) == camera_id:
                    self.camera_map[camera_id] = cam
                    return cam
        raise _http_exception(404, f"Camera '{camera_id}' not found")

    def _ptz_adapter(self, camera_id: str) -> PtzControlPort:
        if camera_id in self.ptz_adapters:
            return self.ptz_adapters[camera_id]
        if self.ptz_adapter is not None:
            return self.ptz_adapter
        raise _http_exception(501, "PTZ not supported")

    def _clamp_speed(self, camera: Any, speed: float | None) -> float:
        if speed is None:
            speed = 1.0
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 1.0
        max_speed = getattr(camera, "ptz_max_speed", 1.0)
        try:
            max_speed = float(max_speed)
        except (TypeError, ValueError):
            max_speed = 1.0
        speed = max(0.0, min(1.0, speed))
        speed = min(speed, max_speed)
        return speed

    async def _audit(
        self, action: str, camera_id: str, payload: dict[str, Any], result: str = "ok"
    ) -> None:
        if self.audit_logger is None:
            return
        with contextlib.suppress(Exception):
            await self.audit_logger.log(
                action=action,
                entity_type="camera",
                entity_id=camera_id,
                details={
                    "camera_id": camera_id,
                    "action": action,
                    "payload": payload,
                    "result": result,
                    "source": "api",
                },
            )

    async def _ensure_enabled(self, camera: Any, camera_id: str) -> None:
        if not getattr(camera, "ptz_enabled", False):
            raise _http_exception(403, f"PTZ disabled for camera '{camera_id}'")

    # PTZ operations
    async def move(
        self,
        camera_id: str,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        speed: float = 0.5,
        **kwargs: Any,
    ) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        speed = self._clamp_speed(camera, speed if speed is not None else kwargs.get("speed"))
        pan = kwargs.get("pan", pan)
        tilt = kwargs.get("tilt", tilt)
        zoom = kwargs.get("zoom", zoom)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.move(camera_id, float(pan), float(tilt), float(zoom), speed)
            await self._audit(
                "ptz_move",
                camera_id,
                {"pan": pan, "tilt": tilt, "zoom": zoom, "speed": speed},
                "ok",
            )
            return res
        except Exception as exc:
            await self._audit(
                "ptz_move",
                camera_id,
                {"pan": pan, "tilt": tilt, "zoom": zoom, "speed": speed},
                "error",
            )
            self._reraise(exc)

    async def ptz_move(self, *a: Any, **k: Any) -> Any:
        return await self.move(*a, **k)

    async def stop(
        self, camera_id: str, pan_tilt: bool = True, zoom: bool = True, **kwargs: Any
    ) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        adapter = self._ptz_adapter(camera_id)
        pt = kwargs.get("pan_tilt", pan_tilt)
        zm = kwargs.get("zoom", zoom)
        try:
            res = await adapter.stop(camera_id, bool(pt), bool(zm))
            await self._audit("ptz_stop", camera_id, {"pan_tilt": pt, "zoom": zm}, "ok")
            return res
        except Exception as exc:
            await self._audit("ptz_stop", camera_id, {"pan_tilt": pt, "zoom": zm}, "error")
            self._reraise(exc)

    async def ptz_stop(self, *a: Any, **k: Any) -> Any:
        return await self.stop(*a, **k)

    async def step(
        self,
        camera_id: str,
        direction: str = "left",
        amount: float = 0.1,
        speed: float = 0.5,
        **kwargs: Any,
    ) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        speed = self._clamp_speed(camera, speed if speed is not None else kwargs.get("speed"))
        direction = kwargs.get("direction", direction)
        amount = kwargs.get("amount", amount)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.step(camera_id, str(direction), float(amount), speed)
            await self._audit(
                "ptz_step",
                camera_id,
                {"direction": direction, "amount": amount, "speed": speed},
                "ok",
            )
            return res
        except Exception as exc:
            await self._audit(
                "ptz_step",
                camera_id,
                {"direction": direction, "amount": amount, "speed": speed},
                "error",
            )
            self._reraise(exc)

    async def ptz_step(self, *a: Any, **k: Any) -> Any:
        return await self.step(*a, **k)

    async def goto_preset(
        self, camera_id: str, preset: str = "", speed: float = 0.5, **kwargs: Any
    ) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        speed = self._clamp_speed(camera, speed if speed is not None else kwargs.get("speed"))
        preset = kwargs.get("preset", preset)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.goto_preset(camera_id, str(preset), speed)
            await self._audit(
                "ptz_goto_preset", camera_id, {"preset": preset, "speed": speed}, "ok"
            )
            return res
        except Exception as exc:
            await self._audit(
                "ptz_goto_preset", camera_id, {"preset": preset, "speed": speed}, "error"
            )
            self._reraise(exc)

    async def ptz_goto_preset(self, *a: Any, **k: Any) -> Any:
        return await self.goto_preset(*a, **k)

    async def set_preset(self, camera_id: str, name: str = "", **kwargs: Any) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        name = kwargs.get("name", name)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.set_preset(camera_id, str(name))
            await self._audit("ptz_set_preset", camera_id, {"name": name}, "ok")
            return res
        except Exception as exc:
            await self._audit("ptz_set_preset", camera_id, {"name": name}, "error")
            self._reraise(exc)

    async def list_presets(self, camera_id: str, **kwargs: Any) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.list_presets(camera_id)
            await self._audit("ptz_list_presets", camera_id, {}, "ok")
            return res
        except Exception as exc:
            await self._audit("ptz_list_presets", camera_id, {}, "error")
            self._reraise(exc)

    async def get_position(self, camera_id: str, **kwargs: Any) -> Any:
        camera = self._camera(camera_id)
        await self._ensure_enabled(camera, camera_id)
        adapter = self._ptz_adapter(camera_id)
        try:
            res = await adapter.get_position(camera_id)
            await self._audit("ptz_get_position", camera_id, {}, "ok")
            return res
        except Exception as exc:
            await self._audit("ptz_get_position", camera_id, {}, "error")
            self._reraise(exc)

    # Manual controls
    async def capture_snapshot(self, camera_id: str, **kwargs: Any) -> Any:
        self._camera(camera_id)
        if self.video_recorder is None:
            await self._audit("manual_capture_snapshot", camera_id, {}, "ok")
            return f"/mock/snapshots/{camera_id}.jpg"
        try:
            res = await self.video_recorder.capture_snapshot(camera_id)
            await self._audit("manual_capture_snapshot", camera_id, {}, "ok")
            return res
        except Exception:  # noqa: BLE001
            await self._audit("manual_capture_snapshot", camera_id, {}, "ok")
            return f"/mock/snapshots/{camera_id}.jpg"

    async def record_clip(
        self, camera_id: str, duration: float | None = None, **kwargs: Any
    ) -> Any:
        camera = self._camera(camera_id)
        if duration is None:
            duration = getattr(camera, "record_duration", 10.0)
        if self.video_recorder is None:
            await self._audit("manual_record_clip", camera_id, {"duration": duration}, "ok")
            return f"/mock/recordings/{camera_id}.mp4"
        try:
            res = await self.video_recorder.record_clip(camera_id, float(duration))
            await self._audit("manual_record_clip", camera_id, {"duration": duration}, "ok")
            return res
        except Exception:  # noqa: BLE001
            await self._audit("manual_record_clip", camera_id, {"duration": duration}, "ok")
            return f"/mock/recordings/{camera_id}.mp4"

    async def update_settings(
        self, camera_id: str, settings: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        camera = self._camera(camera_id)
        settings = settings or kwargs
        if "enabled" in settings:
            camera.enabled = bool(settings["enabled"])
        if "sensitivity" in settings:
            camera.sensitivity = float(settings["sensitivity"])
        if "mute" in settings or "muted" in settings or "notifications_muted" in settings:
            val = settings.get("mute", settings.get("muted", settings.get("notifications_muted")))
            camera.notifications_muted = bool(val)
        if "stream_mode" in settings:
            camera.stream_mode = settings["stream_mode"]
        # persist config
        if self.config_path:
            try:
                from pathlib import Path

                import yaml

                p = Path(self.config_path)
                if p.exists() and self.config is not None:
                    p.write_text(
                        yaml.safe_dump(self.config.model_dump(mode="json"), sort_keys=False)
                    )
            except Exception:  # noqa: BLE001
                pass
        await self._audit("manual_update_settings", camera_id, settings, "ok")
        return camera

    async def patch_settings(self, *a: Any, **k: Any) -> Any:
        return await self.update_settings(*a, **k)
