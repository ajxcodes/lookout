"""Embedded go2rtc stream manager for native Wyze and Ring ingestion."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import socket
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lookout.config.schema import resolve_record_audio
from lookout.ports.egress import VideoRecorderPort

logger = logging.getLogger(__name__)

_JPEG_MAGIC = b"\xff\xd8"
_MP4_FTYP = b"ftyp"
_MP4_MDAT = b"mdat"

_VIDEO_CODECS = ("H264", "H265", "VP8", "VP9", "AV1", "MJPEG")

# Stream-health error classification patterns. All reported strings pass through
# :func:`redact_error` before leaving this module, so no credential material is
# ever written to logs or status.
_ERROR_DISCOVERY_TIMEOUT = "discovery timeout"
_ERROR_CONNECT_FAILED = "connect failed"
_ERROR_AUTHZ = ("406", "ext_authz_denied", "not acceptable", "auth")
_ERROR_SOURCE = ("enr", "uid", "p2p_id", "mac", "ip", "identity", "field")
_ERROR_LOG_KEY = "stream="
_DEFAULT_RECONNECT_BACKOFF = 30.0
_MAX_RECONNECT_BACKOFF = 300.0
_LOG_TAIL_LINES = 200

_SENSITIVE_KV_RE = re.compile(
    r"\b(refresh[-_]?token|access[-_]?token|api[-_]?key|p2p_id|password|passwd|secret|"
    r"credential|token|auth|sig|uid|enr|mac|did)\b\s*[=:]\s*([^\s,&\"']+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE)
_CLI_TOKEN_RE = re.compile(
    r"(--(?:refresh[-_]?token|token|password|secret|key)(?:=|\s+))(\S+)", re.IGNORECASE
)
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def redact_error(text: Any) -> str:
    """Redact credential-like values, MACs and IPs from an error string."""
    if not text:
        return ""
    result = str(text)
    result = _SENSITIVE_KV_RE.sub(lambda m: f"{m.group(1)}=[redacted]", result)
    result = _BEARER_RE.sub(r"\1[redacted]", result)
    result = _CLI_TOKEN_RE.sub(r"\1[redacted]", result)
    result = _MAC_RE.sub("[redacted]", result)
    result = _IPV4_RE.sub("[redacted]", result)
    return result


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
        rtsp_port: int | None = None,
        webrtc_port: int | None = None,
        webrtc_candidate: str | None = None,
        client: httpx.AsyncClient | None = None,
        process_factory: Callable[..., Awaitable[Any]] | None = None,
        readiness_timeout: float = 12.0,
        **kwargs: Any,
    ) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        if config is None and args:
            config = args[0] if hasattr(args[0], "go2rtc") else None
        self.config = config or kwargs.get("config")
        self.record_audio = resolve_record_audio(None, self.config)

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
        self.rtsp_port = int(
            rtsp_port
            or getattr(go2rtc_cfg, "native_rtsp_port", None)
            or os.environ.get("LOOKOUT_RTSP_PORT")
            or 18556
        )
        self.webrtc_port = int(
            webrtc_port
            or getattr(go2rtc_cfg, "native_webrtc_port", None)
            or os.environ.get("LOOKOUT_WEBRTC_PORT")
            or 8556
        )
        self.webrtc_candidate = (
            webrtc_candidate
            or getattr(go2rtc_cfg, "native_webrtc_candidate", None)
            or os.environ.get("LOOKOUT_WEBRTC_CANDIDATE")
            or None
        )
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
        # set of camera ids that should stay connected with no consumer (preload)
        self.recorded_cameras: set[str] = set()
        # Per-camera health / reconnect state (all strings redacted).
        self._stream_health: dict[str, dict[str, Any]] = {}
        self._next_retry: dict[str, float] = {}
        self._last_cooldown_log: dict[str, float] = {}

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

    def set_recorded_cameras(self, cameras: Any) -> None:
        """Set the set of camera ids that should be preloaded in go2rtc."""
        try:
            self.recorded_cameras = {str(c) for c in (cameras or [])}
        except TypeError:
            self.recorded_cameras = {str(cameras)} if cameras is not None else set()

    def _write_config(self) -> None:
        self._ensure_runtime_dir()
        config_path = self.runtime_dir / "go2rtc.yaml"
        lines = [
            "log:",
            "  level: info",
            "api:",
            f'  listen: "127.0.0.1:{self.api_port}"',
            "rtsp:",
            f'  listen: "127.0.0.1:{self.rtsp_port}"',
            "webrtc:",
            f'  listen: ":{self.webrtc_port}"',
        ]
        if self.webrtc_candidate:
            candidate = self.webrtc_candidate
            if ":" not in candidate:
                candidate = f"{candidate}:{self.webrtc_port}"
            lines.extend(
                [
                    "  candidates:",
                    f'    - "{candidate}"',
                    f'    - "stun:{self.webrtc_port}"',
                ]
            )
        lines.append("streams:")
        if self.streams:
            for name, source in self.streams.items():
                escaped = source.replace('"', '\\"')
                lines.append(f'  "{name}": "{escaped}"')
        # An empty mapping must be left as a bare `streams:` key. Writing `  {}`
        # makes go2rtc's own YAML patcher fail (`did not find expected key`) when
        # a stream is added through `PUT /api/streams`.
        if self.recorded_cameras:
            lines.append("preload:")
            for name in sorted(self.recorded_cameras):
                lines.append(f"  {name}: video&audio")
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
            await self._stop_process_locked(clear_streams=True)
        return {"status": "ok"}

    async def _stop_process_locked(self, *, clear_streams: bool = True) -> None:
        process, self._process = self._process, None
        self._started = False
        self._api_url = None
        if clear_streams:
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

    async def _restart_process_locked(self) -> None:
        await self._stop_process_locked(clear_streams=False)
        await self._start_process()

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

    async def _register_via_config_locked(self, camera_id: str, source: str) -> None:
        self.streams[camera_id] = source
        await self._restart_process_locked()
        if not await self._stream_exists(camera_id):
            raise MediaCaptureError(
                f"go2rtc did not register stream '{camera_id}' after config reload"
            )

    async def _ensure_stream_locked(self, camera_id: str, source: str) -> None:
        if self.streams.get(camera_id) == source:
            if await self._stream_exists(camera_id):
                return
            logger.info("go2rtc dropped stream '%s'; re-registering", camera_id)
        if source.startswith("exec:"):
            await self._register_via_config_locked(camera_id, source)
            return
        client = await self._get_client()
        resp = await client.put(
            f"{self._api_url}/api/streams",
            params={"name": camera_id, "src": source},
        )
        if resp.status_code == 400 and "insecure producer" in resp.text:
            await self._register_via_config_locked(camera_id, source)
            return
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

    async def stream_codecs(self, camera_id: str) -> list[str]:
        """Return video codec names go2rtc reports for a stream's producers."""
        client = await self._get_client()
        resp = await client.get(f"{self._api_url}/api/streams", params={"src": str(camera_id)})
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        if isinstance(data, dict) and "producers" not in data and str(camera_id) in data:
            data = data[str(camera_id)]
        codecs: list[str] = []
        producers = data.get("producers") if isinstance(data, dict) else None
        for producer in producers or []:
            if not isinstance(producer, dict):
                continue
            entries: list[Any] = list(producer.get("medias") or []) + list(
                producer.get("codecs") or []
            )
            for entry in entries:
                text = entry if isinstance(entry, str) else str(entry)
                lowered = text.lower()
                if isinstance(entry, str) and lowered.lstrip().startswith(("audio", "application")):
                    continue
                if isinstance(entry, str):
                    # e.g. "video, recvonly, H264, H265" or "H264/90000"
                    for token in text.replace("/", ",").split(","):
                        name = token.strip().upper()
                        if name in _VIDEO_CODECS:
                            codecs.append(name)
                elif isinstance(entry, dict):
                    kind = str(entry.get("media") or entry.get("kind") or "video").lower()
                    name = str(entry.get("codec_name") or "").upper()
                    if kind == "video" and name in _VIDEO_CODECS:
                        codecs.append(name)
        codecs = list(dict.fromkeys(codecs))
        self._stream_health.setdefault(str(camera_id), {})["codecs"] = codecs
        return codecs

    async def has_h264(self, camera_id: str) -> bool:
        """True only if go2rtc serves an H264 video codec; warns otherwise."""
        codecs = await self.stream_codecs(camera_id)
        if "H264" in codecs:
            return True
        logger.warning("Stream '%s' does not serve H264 (codecs=%s)", camera_id, codecs)
        return False

    async def ensure_h264_transcode(self, camera_id: str) -> bool:
        """Add an ffmpeg H.264 transcoding producer when a stream does not serve H264."""
        cam_id = str(camera_id)
        codecs = await self.stream_codecs(cam_id)
        if "H264" in codecs:
            return False
        source = self.streams.get(cam_id)
        if not source:
            return False
        raw_cam_id = f"{cam_id}_raw"
        transcode_src = (
            f"ffmpeg:{raw_cam_id}#video=h264#audio=aac"
            "#raw=-probesize 5000000 -analyzeduration 5000000"
        )
        client = await self._get_client()
        raw_resp = await client.put(
            f"{self._api_url}/api/streams",
            params={"name": raw_cam_id, "src": source},
        )
        if raw_resp.status_code not in (200, 201, 204, 400):
            raise MediaCaptureError(
                f"go2rtc rejected raw stream for '{cam_id}' "
                f"(HTTP {raw_resp.status_code}): {raw_resp.text[:200]}"
            )
        resp = await client.put(
            f"{self._api_url}/api/streams",
            params={"name": cam_id, "src": transcode_src},
        )
        if resp.status_code not in (200, 201, 204, 400):
            raise MediaCaptureError(
                f"go2rtc rejected H264 transcode for '{cam_id}' "
                f"(HTTP {resp.status_code}): {resp.text[:200]}"
            )
        return True

    async def ensure_stream(
        self, camera_id: str, source: str | None = None, force: bool = False
    ) -> None:
        """Register or update a camera stream, starting go2rtc when needed.

        Repeat registrations inside a failure cooldown are skipped (unless
        ``force``) so an unreachable producer such as ``office_cam`` cannot spam
        go2rtc's log with a discovery-timeout retry on every live request.
        """
        cam_id = str(camera_id)
        if not source:
            source = self.streams.get(cam_id)
        if not source:
            raise MediaCaptureError(f"No stream source configured for camera '{camera_id}'")
        next_retry = self._next_retry.get(cam_id)
        if not force and next_retry is not None and time.monotonic() < next_retry:
            last_log = self._last_cooldown_log.get(cam_id, 0.0)
            if time.monotonic() - last_log >= max(1.0, self.retry_after(cam_id)):
                logger.debug(
                    "Stream '%s' is cooling down for %.1fs; skipping re-registration",
                    cam_id,
                    self.retry_after(cam_id),
                )
                self._last_cooldown_log[cam_id] = time.monotonic()
            return
        async with self._lock:
            if not self.running:
                await self._start_process()
            await self._ensure_stream_locked(cam_id, str(source))
        # A successful (re)registration clears any previous failure cooldown.
        self._clear_stream_failure(cam_id)

    async def ensure_h264_stream(self, camera_id: Any, source: Any = None) -> None:
        """Register (or update) a transcoded H.264 stream for a camera.

        The stream is registered under ``{camera_id}_h264`` with source
        ``ffmpeg:{camera_id}#video=h264#audio=aac``. Delegates to
        :meth:`ensure_stream` so idempotency and re-registration semantics are
        preserved.
        """
        normalized = str(camera_id)
        if shutil.which("ffmpeg") is None:
            raise Go2RtcUnavailableError(
                "ffmpeg binary not found; cannot register H.264 transcoded stream "
                f"for camera '{normalized}'"
            )
        h264_name = f"{normalized}_h264"
        h264_source = f"ffmpeg:{normalized}#video=h264#audio=aac"
        await self.ensure_stream(h264_name, h264_source)

    # Compatibility aliases for H.264 transcoding helpers.
    async def ensure_transcoded_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def register_h264_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def register_transcoded_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def start_h264_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def create_h264_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def add_h264_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def ensure_ring_h264_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def transcode_stream(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def ensure_h264(self, *args: Any, **kwargs: Any) -> None:
        camera_id = kwargs.get("camera_id", args[0] if args else None)
        await self.ensure_h264_stream(camera_id, kwargs.get("source"))

    async def remove_stream(self, camera_id: str) -> None:
        """Remove a stream from go2rtc; unknown streams are a clean no-op."""
        async with self._lock:
            self.streams.pop(str(camera_id), None)
            if not self.running:
                return
            client = await self._get_client()
            try:
                await client.delete(f"{self._api_url}/api/streams", params={"src": str(camera_id)})
            except Exception as exc:
                logger.warning(
                    "Failed to remove go2rtc stream '%s' during cleanup: %s",
                    camera_id,
                    exc,
                )

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
        cam_id = str(camera_id)
        try:
            content = await self._request_media(
                "/api/frame.jpeg", {"src": cam_id}, error_context="Snapshot"
            )
            if not content.startswith(_JPEG_MAGIC):
                raise MediaCaptureError(
                    f"Snapshot for '{cam_id}' is not a JPEG ({len(content)} bytes)"
                )
        except MediaCaptureError as exc:
            self.record_stream_error(cam_id, exc)
            raise
        self._clear_stream_failure(cam_id)
        return content

    async def negotiate_consumer(self, camera_id: str) -> None:
        """Prime an on-demand stream by forcing a frame/keyframe capture.

        Calls :meth:`frame` (``/api/frame.jpeg``) which makes go2rtc negotiate
        the producer and cache SPS/picture dimensions. Best-effort callers may
        suppress :class:`MediaCaptureError`; the prime never fabricates media.
        """
        await self.frame(str(camera_id))

    async def clip(self, camera_id: str, duration_seconds: float, retries: int = 3) -> bytes:
        """Record an MP4 clip, retrying with consumer priming on header-only output."""
        cam_id = str(camera_id)
        params: dict[str, Any] = {
            "src": cam_id,
            "duration": max(1, int(round(duration_seconds))),
        }
        if resolve_record_audio(cam_id, self.config):
            params["mp4"] = "all"
        attempts = max(1, int(retries))
        last_error: MediaCaptureError | None = None
        for attempt in range(1, attempts + 1):
            try:
                content = await self._request_media("/api/stream.mp4", params, error_context="Clip")
                if _MP4_FTYP not in content[:64]:
                    raise MediaCaptureError(
                        f"Clip for '{cam_id}' is not an MP4 ({len(content)} bytes)"
                    )
                if _MP4_MDAT not in content:
                    raise MediaCaptureError(
                        f"Clip for '{cam_id}' contains no media samples ({len(content)} bytes); "
                        "the source produced no frames"
                    )
                self._clear_stream_failure(cam_id)
                return content
            except MediaCaptureError as exc:
                last_error = exc
                self.record_stream_error(cam_id, exc)
                if attempt < attempts:
                    with contextlib.suppress(MediaCaptureError):
                        await self.negotiate_consumer(cam_id)
                    await asyncio.sleep(0.1 * attempt)
        raise (
            last_error
            if last_error is not None
            else MediaCaptureError(f"Clip for '{cam_id}' failed")
        )

    # ------------------------------------------------------------------
    # Stream health / reconnect backoff
    # ------------------------------------------------------------------
    def _backoff_base(self, camera_id: str) -> float:
        """Return the configured reconnect backoff base for a camera's provider."""
        provider: str | None = None
        for camera in getattr(self.config, "cameras", None) or []:
            if str(getattr(camera, "id", "")) == str(camera_id):
                provider = getattr(camera, "native_provider", None)
                break
        provider_cfg = getattr(self.config, str(provider), None) if provider else None
        value = getattr(provider_cfg, "stream_reconnect_backoff_seconds", None)
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        return _DEFAULT_RECONNECT_BACKOFF

    def _clear_stream_failure(self, camera_id: str) -> None:
        cam_id = str(camera_id)
        self._next_retry.pop(cam_id, None)
        self._last_cooldown_log.pop(cam_id, None)
        health = self._stream_health.setdefault(cam_id, {})
        health["consecutive_failures"] = 0
        health["last_error"] = None
        health["retry_after_seconds"] = 0.0

    def record_stream_error(self, camera_id: str, error: Any) -> None:
        """Record a redacted stream error and schedule exponential reconnect backoff."""
        cam_id = str(camera_id)
        health = self._stream_health.setdefault(cam_id, {})
        failures = int(health.get("consecutive_failures") or 0) + 1
        health["consecutive_failures"] = failures
        health["last_error"] = redact_error(error)
        health["last_error_at"] = datetime.now(UTC).isoformat()
        base = self._backoff_base(cam_id)
        delay = min(_MAX_RECONNECT_BACKOFF, base * (2 ** (failures - 1))) if base > 0 else 0.0
        health["retry_after_seconds"] = float(delay)
        self._next_retry[cam_id] = time.monotonic() + delay

    def retry_after(self, camera_id: str) -> float:
        """Seconds remaining before a camera may be re-registered (0 when clear)."""
        next_retry = self._next_retry.get(str(camera_id))
        if next_retry is None:
            return 0.0
        return float(max(0.0, next_retry - time.monotonic()))

    def _tail_log_lines(self) -> list[str]:
        """Return at most the last 200 lines of the embedded go2rtc log."""
        path = self.runtime_dir / "go2rtc.log"
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.readlines()[-_LOG_TAIL_LINES:]
        except (OSError, ValueError):
            return []

    def _log_error_for(self, camera_id: str) -> str | None:
        cam_id = str(camera_id)
        for line in reversed(self._tail_log_lines()):
            if _ERROR_LOG_KEY not in line and cam_id not in line:
                continue
            match = re.search(r'error="([^"]*)"', line)
            detail = match.group(1) if match else line.strip()
            if (
                detail
                and cam_id in line
                and (
                    _ERROR_DISCOVERY_TIMEOUT in detail.lower()
                    or _ERROR_CONNECT_FAILED in detail.lower()
                    or _ERROR_LOG_KEY in line
                )
            ):
                return detail
        return None

    def _classify_error(self, camera_id: str, error: str | None = None) -> str:
        cam_id = str(camera_id)
        text = (error or "").lower()
        log_error = self._log_error_for(cam_id)
        if log_error:
            text = f"{text} {log_error}".lower()
        # Missing identity fields reported by source verification.
        if "missing" in text and any(field in text for field in _ERROR_SOURCE):
            return "source_error"
        if _ERROR_DISCOVERY_TIMEOUT in text or _ERROR_CONNECT_FAILED in text:
            return "unreachable"
        if any(marker in text for marker in _ERROR_AUTHZ):
            return "auth"
        health = self._stream_health.get(cam_id, {})
        if text and not health.get("codecs"):
            return "no_media"
        return "unknown"

    def stream_health(self, camera_id: str) -> dict[str, Any]:
        """Return a redacted, actionable health snapshot for one camera."""
        cam_id = str(camera_id)
        health = self._stream_health.get(cam_id, {})
        log_error = self._log_error_for(cam_id)
        raw_error = health.get("last_error") or log_error
        error = redact_error(raw_error) if raw_error else None
        classification = self._classify_error(cam_id, raw_error) if raw_error else "unknown"
        failures = int(health.get("consecutive_failures") or 0)
        if classification == "unreachable" or (classification == "no_media" and failures):
            status = "unavailable"
        elif failures:
            status = "degraded"
        else:
            status = "ok"
        recommendation = None
        if status != "ok":
            recommendation = (
                "Verify native discovery for this camera, check device power/network "
                "reachability, set a per-camera stream_url override, or try "
                "wyze.stream_dtls=false."
            )
        return {
            "camera_id": cam_id,
            "source": redact_error(self.streams.get(cam_id)),
            "codecs": list(health.get("codecs") or []),
            "status": status,
            "classification": classification,
            "error": error,
            "consecutive_failures": failures,
            "retry_after_seconds": float(
                health.get("retry_after_seconds") or self.retry_after(cam_id)
            ),
            "recommendation": recommendation,
        }

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
