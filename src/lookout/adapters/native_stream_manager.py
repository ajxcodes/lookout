"""Embedded go2rtc stream manager for native Wyze and Ring ingestion."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from lookout.ports.egress import VideoRecorderPort

logger = logging.getLogger(__name__)

_JPEG_MAGIC = b"\xff\xd8"
_MP4_FTYP = b"ftyp"
_MP4_MDAT = b"mdat"


class Go2RtcUnavailableError(RuntimeError):
    """Raised when no usable go2rtc binary can be located."""


class MediaCaptureError(RuntimeError):
    """Raised when a native stream cannot produce valid media."""


class NativeStreamManager(VideoRecorderPort):
    """Own an embedded go2rtc process and expose per-camera media helpers.

    The manager resolves a go2rtc binary, writes a loopback-only configuration,
    starts the process, and proxies frame/clip requests through its HTTP API.
    It deliberately never fabricates media: every failure raises
    :class:`MediaCaptureError`.
    """

    def __init__(
        self,
        *args: Any,
        config: Any | None = None,
        binary_path: str | None = None,
        runtime_dir: str | Path | None = None,
        api_port: int | None = None,
        client: httpx.AsyncClient | None = None,
        process_factory: Callable[..., Awaitable[Any]] | None = None,
        readiness_timeout: float = 12.0,
        **kwargs: Any,
    ) -> None:
        if config is None and args:
            config = args[0] if hasattr(args[0], "go2rtc") else None
        self.config = config or kwargs.get("config")

        go2rtc_cfg = getattr(self.config, "go2rtc", None)
        self.binary_path = (
            binary_path
            or getattr(go2rtc_cfg, "binary_path", None)
            or os.environ.get("LOOKOUT_GO2RTC_BINARY")
        )
        self.runtime_dir = Path(
            runtime_dir
            or getattr(go2rtc_cfg, "native_runtime_dir", None)
            or os.environ.get("LOOKOUT_GO2RTC_DIR")
            or Path(tempfile.gettempdir()) / "lookout-go2rtc"
        )
        self.api_port = int(api_port or getattr(go2rtc_cfg, "native_api_port", 11984) or 11984)
        self.readiness_timeout = readiness_timeout
        self._external_client = client
        self._client: httpx.AsyncClient | None = client
        self._process_factory = process_factory
        self._process: Any | None = None
        self._log_handle: Any | None = None
        self._api_url: str | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self.last_error: str | None = None
        # camera_id -> go2rtc source URL
        self.streams: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """Whether the embedded go2rtc process is alive."""
        return self._process is not None and getattr(self._process, "returncode", None) is None

    @property
    def api_url(self) -> str | None:
        """Loopback API base URL, available once started."""
        return self._api_url

    def _resolve_binary(self) -> str:
        candidates: list[str] = []
        if self.binary_path:
            candidates.append(str(self.binary_path))
        bundled = Path(__file__).parent / "bin" / "go2rtc"
        candidates.append(str(bundled))
        candidates.append("/usr/local/bin/go2rtc")
        found = shutil.which("go2rtc")
        if found:
            candidates.append(found)
        for candidate in candidates:
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        raise Go2RtcUnavailableError(
            "go2rtc binary not found. Set go2rtc.binary_path, LOOKOUT_GO2RTC_BINARY, "
            "install go2rtc on PATH, or use the Lookout Docker image (which bundles it)."
        )

    def _pick_port(self) -> int:
        for port in (self.api_port, 0):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return int(sock.getsockname()[1])
        raise Go2RtcUnavailableError("Could not allocate a loopback port for go2rtc")

    def _ensure_runtime_dir(self) -> None:
        """Create the runtime directory with owner-only permissions."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            os.chmod(self.runtime_dir, 0o700)

    def _write_config(self) -> None:
        self._ensure_runtime_dir()
        config_path = self.runtime_dir / "go2rtc.yaml"
        lines = [
            "log:",
            "  level: info",
            "api:",
            f'  listen: "127.0.0.1:{self.api_port}"',
            "rtsp:",
            '  listen: ""',
            "webrtc:",
            '  listen: ""',
            "streams:",
        ]
        if self.streams:
            for name, source in self.streams.items():
                escaped = source.replace('"', '\\"')
                lines.append(f'  "{name}": "{escaped}"')
        # An empty mapping must be left as a bare `streams:` key. Writing `  {}`
        # makes go2rtc's own YAML patcher fail (`did not find expected key`) when
        # a stream is added through `PUT /api/streams`.
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(config_path, 0o600)

    async def _default_process_factory(self, binary: str, config_path: Path) -> Any:
        self._ensure_runtime_dir()
        log_path = self.runtime_dir / "go2rtc.log"
        # go2rtc logs to stdout, so both streams must point at the same handle.
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with contextlib.suppress(OSError):
            os.chmod(log_path, 0o600)
        self._log_handle = os.fdopen(fd, "ab")
        return await asyncio.create_subprocess_exec(
            binary,
            "-c",
            str(config_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=self._log_handle,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout())
        return self._client

    def _timeout(self) -> float:
        return float(getattr(getattr(self.config, "go2rtc", None), "timeout_seconds", 10.0) or 10.0)

    async def _wait_ready(self) -> None:
        client = await self._get_client()
        deadline = asyncio.get_running_loop().time() + self.readiness_timeout
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if not self.running:
                break
            try:
                resp = await client.get(f"{self._api_url}/api/streams")
                if resp.status_code == 200:
                    return
            except Exception as exc:  # noqa: BLE001 - readiness probe
                last_error = exc
            await asyncio.sleep(0.2)
        detail = f": {last_error}" if last_error else ""
        raise Go2RtcUnavailableError(f"go2rtc did not become ready{detail}")

    async def _start_process(self) -> None:
        if self.running:
            return
        binary = self._resolve_binary()
        self.api_port = self._pick_port()
        self._api_url = f"http://127.0.0.1:{self.api_port}"
        self._write_config()
        config_path = self.runtime_dir / "go2rtc.yaml"
        factory = self._process_factory or self._default_process_factory
        self._process = await factory(binary, config_path)
        self._started = True
        self.last_error = None
        await self._wait_ready()
        logger.info("Embedded go2rtc listening on %s (binary=%s)", self._api_url, binary)

    async def start(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Start go2rtc, or add a stream when called with camera/source arguments."""
        camera_id, source = self._extract_stream_args(*args, **kwargs)
        async with self._lock:
            if not self.running:
                await self._start_process()
            if camera_id and source:
                await self._ensure_stream_locked(camera_id, source)
        if camera_id:
            return self._stream_status(camera_id)
        return await self.status()

    async def stop(self, camera_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Stop one stream, or the whole embedded process when no camera is given."""
        camera_id = camera_id or kwargs.get("camera_id")
        if camera_id:
            await self.remove_stream(str(camera_id))
            return {"status": "ok", "camera_id": str(camera_id)}
        async with self._lock:
            process, self._process = self._process, None
            self._started = False
            self._api_url = None
            self.streams.clear()
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await process.wait()
            if self._external_client is None and self._client is not None:
                await self._client.aclose()
                self._client = None
            if self._log_handle is not None:
                with contextlib.suppress(Exception):
                    self._log_handle.close()
                self._log_handle = None
        return {"status": "ok"}

    async def close(self) -> None:
        """Alias for :meth:`stop` used by application shutdown."""
        await self.stop()

    # ------------------------------------------------------------------
    # Stream management
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_stream_args(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        camera_id: Any = kwargs.get("camera_id")
        source: Any = kwargs.get("source") or kwargs.get("url") or kwargs.get("stream_url")
        if len(args) > 0 and isinstance(args[0], str):
            camera_id = args[0]
        elif len(args) > 0 and isinstance(args[0], dict):
            camera_id = args[0].get("camera_id") or args[0].get("id")
            source = source or args[0].get("source") or args[0].get("url")
        if len(args) > 1 and isinstance(args[1], str):
            source = args[1]
        return (str(camera_id) if camera_id else None, str(source) if source else None)

    async def _ensure_stream_locked(self, camera_id: str, source: str) -> None:
        if self.streams.get(camera_id) == source:
            return
        client = await self._get_client()
        resp = await client.put(
            f"{self._api_url}/api/streams",
            params={"name": camera_id, "src": source},
        )
        # go2rtc 1.9.x can return 400 while still adding the stream because of a
        # response-serialization quirk; confirm against the live stream map.
        if resp.status_code not in (200, 201, 204, 400):
            raise MediaCaptureError(
                f"go2rtc rejected stream '{camera_id}' (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        if not await self._stream_exists(camera_id):
            raise MediaCaptureError(
                f"go2rtc did not register stream '{camera_id}' "
                f"(HTTP {resp.status_code}): {resp.text[:200]}"
            )
        self.streams[camera_id] = source

    async def _stream_exists(self, camera_id: str) -> bool:
        client = await self._get_client()
        resp = await client.get(f"{self._api_url}/api/streams")
        if resp.status_code != 200:
            return False
        try:
            data = resp.json()
        except ValueError:
            return False
        return isinstance(data, dict) and camera_id in data

    async def ensure_stream(self, camera_id: str, source: str | None = None) -> None:
        """Register or update a camera stream, starting go2rtc when needed."""
        if not source:
            source = self.streams.get(str(camera_id))
        if not source:
            raise MediaCaptureError(f"No stream source configured for camera '{camera_id}'")
        async with self._lock:
            if not self.running:
                await self._start_process()
            await self._ensure_stream_locked(str(camera_id), str(source))

    async def remove_stream(self, camera_id: str) -> None:
        """Remove a stream from go2rtc; unknown streams are a clean no-op."""
        async with self._lock:
            self.streams.pop(str(camera_id), None)
            if not self.running:
                return
            client = await self._get_client()
            with contextlib.suppress(Exception):
                await client.delete(f"{self._api_url}/api/streams", params={"src": str(camera_id)})

    # Compatibility aliases used by the previous scaffold and application code.
    async def start_stream(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.start(*args, **kwargs)

    async def start_camera(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.start(*args, **kwargs)

    async def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.start(*args, **kwargs)

    async def stop_stream(self, camera_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return await self.stop(camera_id=camera_id, **kwargs)

    async def stop_camera(self, camera_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return await self.stop(camera_id=camera_id, **kwargs)

    async def configure(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        camera_id, source = self._extract_stream_args(*args, **kwargs)
        if camera_id and source:
            await self.ensure_stream(camera_id, source)
        return self._stream_status(camera_id) if camera_id else await self.status()

    async def configure_stream(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.configure(*args, **kwargs)

    async def set_config(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.configure(*args, **kwargs)

    async def is_running(self, camera_id: str | None = None, **kwargs: Any) -> bool:
        camera_id = camera_id or kwargs.get("camera_id")
        if camera_id:
            return self.running and str(camera_id) in self.streams
        return self.running

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------
    async def _request_media(
        self, path: str, params: dict[str, Any], *, error_context: str
    ) -> bytes:
        if not self.running:
            await self.ensure_stream(str(params.get("src") or ""))
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self._api_url}{path}",
                params=params,
                timeout=max(self._timeout(), 30.0),
            )
        except Exception as exc:  # noqa: BLE001 - normalized to media error
            raise MediaCaptureError(f"{error_context} failed: {exc}") from exc
        if resp.status_code != 200 or not resp.content:
            raise MediaCaptureError(
                f"{error_context} failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        return resp.content

    async def frame(self, camera_id: str) -> bytes:
        """Capture a JPEG frame from a go2rtc stream."""
        content = await self._request_media(
            "/api/frame.jpeg", {"src": str(camera_id)}, error_context="Snapshot"
        )
        if not content.startswith(_JPEG_MAGIC):
            raise MediaCaptureError(
                f"Snapshot for '{camera_id}' is not a JPEG ({len(content)} bytes)"
            )
        return content

    async def negotiate_consumer(self, camera_id: str) -> None:
        """Prime an on-demand stream by forcing a frame/keyframe capture.

        Calls :meth:`frame` and propagates :class:`MediaCaptureError` on failure.
        Safe to call when the stream is already registered.
        """
        await self.frame(str(camera_id))

    async def clip(self, camera_id: str, duration_seconds: float, retries: int = 3) -> bytes:
        """Record an MP4 clip from a go2rtc stream.

        Retries on HTTP 500 / empty / non-MP4 / missing media samples, priming the
        stream with :meth:`negotiate_consumer` between attempts. Raises
        :class:`MediaCaptureError` only after all attempts are exhausted.
        """
        camera_id = str(camera_id)
        params: dict[str, Any] = {
            "src": camera_id,
            "duration": max(1, int(round(duration_seconds))),
        }
        attempts = max(1, int(retries))
        last_error: MediaCaptureError | None = None
        for attempt in range(1, attempts + 1):
            try:
                content = await self._request_media("/api/stream.mp4", params, error_context="Clip")
                if _MP4_FTYP not in content[:64]:
                    raise MediaCaptureError(
                        f"Clip for '{camera_id}' is not an MP4 ({len(content)} bytes)"
                    )
                if _MP4_MDAT not in content:
                    raise MediaCaptureError(
                        f"Clip for '{camera_id}' contains no media samples ({len(content)} bytes); "
                        "the source produced no frames"
                    )
                return content
            except MediaCaptureError as exc:
                last_error = exc
                if attempt < attempts:
                    with contextlib.suppress(MediaCaptureError):
                        await self.negotiate_consumer(camera_id)
                    await asyncio.sleep(0.1 * attempt)
        raise (
            last_error
            if last_error is not None
            else MediaCaptureError(f"Clip for '{camera_id}' failed")
        )

    async def capture_snapshot(self, camera_id: str) -> str:
        """Write a validated JPEG snapshot to a temporary file and return its path."""
        content = await self.frame(camera_id)
        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="lookout-native-")
        os.close(fd)
        Path(path).write_bytes(content)
        return path

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        """Write a validated MP4 clip to a temporary file and return its path."""
        content = await self.clip(camera_id, duration_seconds)
        fd, path = tempfile.mkstemp(suffix=".mp4", prefix="lookout-native-")
        os.close(fd)
        Path(path).write_bytes(content)
        return path

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _stream_status(self, camera_id: str | None = None) -> dict[str, Any]:
        if camera_id is not None:
            return {
                "camera_id": str(camera_id),
                "source": self.streams.get(str(camera_id)),
                "running": self.running,
            }
        return {
            "camera_id": None,
            "running": self.running,
            "streams": dict(self.streams),
        }

    async def status(self, camera_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Return a snapshot of the manager and its registered streams."""
        camera_id = camera_id or kwargs.get("camera_id")
        base: dict[str, Any] = {
            "running": self.running,
            "api_url": self._api_url,
            "streams": dict(self.streams),
            "binary": self.binary_path,
            "error": self.last_error,
        }
        if self.running:
            with contextlib.suppress(Exception):
                client = await self._get_client()
                resp = await client.get(f"{self._api_url}/api/streams")
                if resp.status_code == 200 and isinstance(resp.json(), dict):
                    base["active_streams"] = sorted(resp.json().keys())
        if camera_id:
            base["camera_id"] = str(camera_id)
            base["camera_running"] = str(camera_id) in self.streams
        return base

    async def get_status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        camera_id = args[0] if args and isinstance(args[0], str) else kwargs.get("camera_id")
        return await self.status(camera_id)


class NativeAwareRecorder(VideoRecorderPort):
    """Route media capture to the adapter that owns each camera's provider."""

    def __init__(
        self,
        config: Any | None = None,
        wyze: VideoRecorderPort | None = None,
        ring: VideoRecorderPort | None = None,
        fallback: VideoRecorderPort | None = None,
    ) -> None:
        self.config = config
        self.wyze = wyze
        self.ring = ring
        self.fallback = fallback

    def _provider(self, camera_id: str) -> str | None:
        cameras = getattr(self.config, "cameras", None) or []
        for camera in cameras:
            if str(getattr(camera, "id", "")) == str(camera_id):
                return getattr(camera, "native_provider", None)
        return None

    def _pick(self, camera_id: str) -> VideoRecorderPort:
        provider = (self._provider(camera_id) or "").lower()
        if provider == "wyze" and self.wyze is not None:
            return self.wyze
        if provider == "ring" and self.ring is not None:
            return self.ring
        if self.fallback is not None:
            return self.fallback
        raise MediaCaptureError(
            f"No recorder available for camera '{camera_id}' (provider={provider or 'unknown'})"
        )

    async def capture_snapshot(self, camera_id: str) -> str:
        return await self._pick(camera_id).capture_snapshot(camera_id)

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        return await self._pick(camera_id).record_clip(camera_id, duration_seconds)
