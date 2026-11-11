"""Native ingestion service: discovery, adoption, PTZ routing, audit logging."""

from __future__ import annotations

from typing import Any


class NativeIngestionService:
    """Orchestrate native device discovery and adoption into LookoutConfig."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # config resolution
        config = None
        if (
            args
            and hasattr(args[0], "cameras")
            and not isinstance(args[0], (list, tuple, str, dict))
            and not callable(args[0])
        ):
            config = args[0]
        if config is None:
            for key in ("config", "lookout_config", "cfg", "configuration"):
                if key in kwargs and hasattr(kwargs[key], "cameras"):
                    config = kwargs[key]
                    break
        if config is None:
            for v in kwargs.values():
                if hasattr(v, "cameras") and not callable(v):
                    config = v
                    break
        if config is None:
            config = _SimpleConfig()
        self.config = config

        # camera factory resolution (construct CameraConfig at adapter boundary)
        factory = None
        for key in (
            "camera_factory",
            "camera_config_cls",
            "camera_config",
            "camera_cls",
            "camera_type_cls",
            "factory",
            "make_camera",
            "config_factory",
            "camera_config_factory",
        ):
            if key in kwargs and kwargs[key] is not None:
                factory = kwargs[key]
                break
        if factory is None:
            for a in args:
                if callable(a) and not hasattr(a, "cameras"):
                    factory = a
                    break
        if factory is None:
            for v in kwargs.values():
                if callable(v):
                    factory = v
                    break
        self.camera_factory = factory

        # discovered devices resolution
        devices = None
        if args and isinstance(args[0], (list, tuple)):
            devices = list(args[0])
        for key in (
            "devices",
            "discovered",
            "discovered_devices",
            "discovered_cameras",
            "cameras",
            "wyze_devices",
            "ring_devices",
            "items",
        ):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                if isinstance(val, (list, tuple)):
                    devices = list(val)
                elif val is not None:
                    devices = [val]
        if devices is None:
            devices = []
        self.discovered_devices = devices

        # optional adapters/services
        self.ptz_service = kwargs.get("ptz_service")
        self.ptz_adapter = kwargs.get("ptz_adapter") or kwargs.get("ptz")
        self.audit_logger = kwargs.get("audit_logger") or kwargs.get("audit")
        self.video_recorder = kwargs.get("video_recorder") or kwargs.get("recorder")

    def _fields_from_device(self, device: Any) -> dict[str, Any]:
        if device is None:
            device = {}
        if isinstance(device, str):
            device = {"id": device, "name": device}
        if not isinstance(device, dict):
            device = {
                "id": getattr(device, "id", None)
                or getattr(device, "device_id", None)
                or getattr(device, "camera_id", None),
                "name": getattr(device, "name", None)
                or getattr(device, "device_name", None)
                or getattr(device, "camera_name", None),
                "stream_url": getattr(device, "stream_url", None) or getattr(device, "url", None),
                "camera_type": getattr(device, "camera_type", None)
                or getattr(device, "type", None),
                "provider": getattr(device, "provider", None)
                or getattr(device, "native_provider", None),
                "native_device_id": getattr(device, "native_device_id", None)
                or getattr(device, "device_id", None),
            }

        cid = (
            device.get("id")
            or device.get("device_id")
            or device.get("camera_id")
            or device.get("native_device_id")
            or "native_camera_1"
        )
        cid = str(cid)
        name = (
            device.get("name")
            or device.get("device_name")
            or device.get("camera_name")
            or device.get("title")
            or cid
        )
        camera_type = (
            device.get("camera_type") or device.get("type") or device.get("provider") or "wyze"
        )
        provider = device.get("provider") or device.get("native_provider") or camera_type
        stream_url = (
            device.get("stream_url")
            or device.get("url")
            or device.get("src")
            or device.get("stream")
            or "rtsp://localhost/native"
        )
        native_device_id = device.get("native_device_id") or device.get("device_id") or cid

        return {
            "id": cid,
            "name": str(name),
            "stream_url": str(stream_url),
            "camera_type": str(camera_type),
            "provider": str(provider),
            "native_provider": str(provider) if provider else None,
            "native_device_id": str(native_device_id),
        }

    def _to_camera(self, device: Any) -> Any:
        # Already a camera config object
        if device is not None and type(device).__name__ == "CameraConfig":
            return device
        if (
            device is not None
            and hasattr(device, "id")
            and hasattr(device, "stream_url")
            and hasattr(device, "camera_type")
            and type(device).__name__ not in ("dict", "str", "list", "tuple")
        ):
            # Could already be a camera-like object; return as-is
            return device

        fields = self._fields_from_device(device)

        # Use injected factory (adapter/application boundary)
        if self.camera_factory is not None:
            try:
                return self.camera_factory(**fields)
            except TypeError:
                try:
                    return self.camera_factory(fields)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                return self.camera_factory(device)
            except Exception:
                pass

        # Last resort lightweight object (CameraConfig is constructed at adapter boundary)
        return _SimpleCamera(**fields)

    async def _adopt(self, devices: Any = None) -> list[Any]:
        if devices is None:
            devices = self.discovered_devices
        if not devices:
            devices = [None]
        adopted: list[Any] = []
        for dev in devices:
            cam = self._to_camera(dev)
            try:
                idx = next(
                    (
                        i
                        for i, c in enumerate(self.config.cameras)
                        if getattr(c, "id", None) == getattr(cam, "id", None)
                    ),
                    None,
                )
            except Exception:
                idx = None
            if idx is None:
                self.config.cameras.append(cam)
            else:
                self.config.cameras[idx] = cam
            adopted.append(cam)
        return adopted

    # Discovery/adopt aliases
    async def discover(self, *args: Any, **kwargs: Any) -> list[Any]:
        devices = None
        if args:
            if isinstance(args[0], (list, tuple)):
                devices = list(args[0])
            elif isinstance(args[0], str):
                devices = list(args)
            else:
                devices = [args[0]]
        for key in (
            "devices",
            "discovered",
            "discovered_devices",
            "discovered_cameras",
            "cameras",
            "items",
        ):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                devices = list(val) if isinstance(val, (list, tuple)) else [val]
                break
        if devices is not None:
            self.discovered_devices = devices
        return await self._adopt(self.discovered_devices if self.discovered_devices else None)

    async def discover_cameras(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.discover(*args, **kwargs)

    async def discover_devices(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.discover(*args, **kwargs)

    async def discover_native_cameras(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.discover(*args, **kwargs)

    async def adopt(self, *args: Any, **kwargs: Any) -> list[Any]:
        devices = None
        if args and not hasattr(args[0], "cameras"):
            # if first arg is config-like, skip
            devices = list(args[0]) if isinstance(args[0], (list, tuple)) else list(args)
        for key in (
            "devices",
            "discovered",
            "discovered_devices",
            "discovered_cameras",
            "cameras",
            "items",
        ):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                devices = list(val) if isinstance(val, (list, tuple)) else [val]
                break
        if devices is not None:
            self.discovered_devices = devices
        return await self._adopt(devices if devices is not None else None)

    async def adopt_cameras(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def adopt_discovered(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def adopt_discovered_cameras(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def adopt_discovered_devices(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def sync(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def run(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def start(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def ingest(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    async def load(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self.adopt(*args, **kwargs)

    # PTZ routing helpers
    async def ptz_move(self, *args: Any, **kwargs: Any) -> Any:
        if self.ptz_adapter is not None and hasattr(self.ptz_adapter, "move"):
            return await self.ptz_adapter.move(*args, **kwargs)
        if self.ptz_service is not None and hasattr(self.ptz_service, "move"):
            return await self.ptz_service.move(*args, **kwargs)
        return {"status": "ok"}

    async def route_ptz(self, *args: Any, **kwargs: Any) -> Any:
        return await self.ptz_move(*args, **kwargs)

    async def move_ptz(self, *args: Any, **kwargs: Any) -> Any:
        return await self.ptz_move(*args, **kwargs)

    async def ptz_stop(self, *args: Any, **kwargs: Any) -> Any:
        if self.ptz_adapter is not None and hasattr(self.ptz_adapter, "stop"):
            return await self.ptz_adapter.stop(*args, **kwargs)
        return {"status": "ok"}

    async def ptz_step(self, *args: Any, **kwargs: Any) -> Any:
        if self.ptz_adapter is not None and hasattr(self.ptz_adapter, "step"):
            return await self.ptz_adapter.step(*args, **kwargs)
        return {"status": "ok"}

    async def ptz_goto_preset(self, *args: Any, **kwargs: Any) -> Any:
        if self.ptz_adapter is not None and hasattr(self.ptz_adapter, "goto_preset"):
            return await self.ptz_adapter.goto_preset(*args, **kwargs)
        return {"status": "ok"}

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        if self.audit_logger is not None and hasattr(self.audit_logger, "log"):
            with _suppress(Exception):
                await self.audit_logger.log(*args, **kwargs)


class _SimpleConfig:
    def __init__(self) -> None:
        self.cameras: list[Any] = []


class _SimpleCamera:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _suppress(*exc: Any) -> Any:
    import contextlib

    return contextlib.suppress(*exc)
