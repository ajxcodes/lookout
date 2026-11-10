"""Native stream manager for per-camera stream lifecycle."""

from __future__ import annotations

from typing import Any


class NativeStreamManager:
    """Manage native streams (start/stop/configure/status) per camera."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config")
        self.streams: dict[str, dict[str, Any]] = {}
        self._running = False

    @property
    def running(self) -> bool:
        return bool(self.streams)

    async def start(self, *args: Any, **kwargs: Any) -> Any:
        camera_id = self._camera_id(*args, **kwargs)
        url = self._url(*args, **kwargs)
        if camera_id not in self.streams:
            self.streams[camera_id] = {"camera_id": camera_id, "url": url, "running": True}
        else:
            self.streams[camera_id]["running"] = True
            if url:
                self.streams[camera_id]["url"] = url
        self._running = bool(self.streams)
        return self.streams[camera_id]

    async def start_stream(self, *args: Any, **kwargs: Any) -> Any:
        return await self.start(*args, **kwargs)

    async def start_camera(self, *args: Any, **kwargs: Any) -> Any:
        return await self.start(*args, **kwargs)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        return await self.start(*args, **kwargs)

    async def stop(self, *args: Any, **kwargs: Any) -> Any:
        camera_id = self._camera_id(*args, required=False, **kwargs)
        if camera_id is None or camera_id not in self.streams:
            # stop all if no id
            if camera_id is None:
                self.streams.clear()
            return {"status": "ok"}
        self.streams.pop(camera_id, None)
        return {"status": "ok"}

    async def stop_stream(self, *args: Any, **kwargs: Any) -> Any:
        return await self.stop(*args, **kwargs)

    async def stop_camera(self, *args: Any, **kwargs: Any) -> Any:
        return await self.stop(*args, **kwargs)

    async def configure(self, *args: Any, **kwargs: Any) -> Any:
        camera_id = self._camera_id(*args, **kwargs)
        if camera_id not in self.streams:
            self.streams[camera_id] = {"camera_id": camera_id, "running": False}
        cfg = kwargs.get("config") or kwargs.get("settings") or {}
        if isinstance(cfg, dict):
            self.streams[camera_id].update(cfg)
        # also accept trailing positional dict
        for a in args:
            if isinstance(a, dict) and a is not None:
                self.streams[camera_id].update(a)
        return self.streams[camera_id]

    async def configure_stream(self, *args: Any, **kwargs: Any) -> Any:
        return await self.configure(*args, **kwargs)

    async def set_config(self, *args: Any, **kwargs: Any) -> Any:
        return await self.configure(*args, **kwargs)

    async def status(self, *args: Any, **kwargs: Any) -> Any:
        camera_id = self._camera_id(*args, required=False, **kwargs)
        if camera_id is None:
            return {"running": self.running, "streams": list(self.streams.values())}
        return self.streams.get(camera_id, {"camera_id": camera_id, "running": False})

    async def get_status(self, *args: Any, **kwargs: Any) -> Any:
        return await self.status(*args, **kwargs)

    async def is_running(self, *args: Any, **kwargs: Any) -> bool:
        camera_id = self._camera_id(*args, required=False, **kwargs)
        if camera_id is None:
            return self.running
        return camera_id in self.streams and self.streams[camera_id].get("running", False)

    def _camera_id(self, *args: Any, required: bool = True, **kwargs: Any) -> Any:
        for a in args:
            if isinstance(a, str):
                return a
            if isinstance(a, dict):
                for key in ("camera_id", "cameraId", "id", "device_id"):
                    if key in a and a[key] is not None:
                        return str(a[key])
            if hasattr(a, "id"):
                return str(a.id)
            if hasattr(a, "camera_id"):
                return str(a.camera_id)
        for key in ("camera_id", "cameraId", "id", "device_id", "camera"):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                if isinstance(val, str):
                    return val
                return str(val)
        if required:
            return "stream_1"
        return None

    def _url(self, *args: Any, **kwargs: Any) -> Any:
        for a in args:
            if isinstance(a, str):
                continue
            if isinstance(a, dict):
                for key in ("stream_url", "url", "src"):
                    if key in a and a[key] is not None:
                        return a[key]
        for key in ("stream_url", "url", "src"):
            if key in kwargs and kwargs[key] is not None:
                return kwargs[key]
        return kwargs.get("url")
