"""Native Ring ingestion adapter (REST API + go2rtc) implementing VideoRecorderPort."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lookout.adapters.native_stream_manager import MediaCaptureError, NativeStreamManager
from lookout.config.schema import RingConfig
from lookout.domain.models import CameraEvent, DetectionMetrics, EventCategory, ThreatLevel
from lookout.ports.egress import VideoRecorderPort

logger = logging.getLogger(__name__)

_JPEG_MAGIC = b"\xff\xd8"
_MP4_FTYP = b"ftyp"


class RingAuthError(RuntimeError):
    """Raised when no usable Ring refresh token is configured."""


class RingApiError(RuntimeError):
    """Raised when a Ring cloud API call fails."""


class RingStreamSourceError(RuntimeError):
    """Raised when a Ring device lacks the fields required to build a go2rtc source."""


def decode_ring_token(raw: str) -> dict[str, Any]:
    """Decode the base64-wrapped outer Ring token into its ``{rt,hid,pnc}`` payload."""
    if not raw:
        raise RingAuthError("Ring refresh token is empty")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        inner = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RingAuthError(f"Invalid Ring refresh token: {exc}") from exc
    if not isinstance(inner, dict) or not inner.get("rt"):
        raise RingAuthError("Ring refresh token is missing the 'rt' field")
    return inner


def encode_ring_token(inner: dict[str, Any]) -> str:
    """Encode a ``{rt,hid,pnc}`` payload back into the base64 outer token."""
    return base64.urlsafe_b64encode(
        json.dumps(inner, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _category_from(kind: Any, detection_type: Any) -> EventCategory:
    text = f"{kind or ''} {detection_type or ''}".lower()
    if "package" in text:
        return EventCategory.PACKAGE_EVENT
    if "vehicle" in text or "car" in text:
        return EventCategory.VEHICLE
    if "animal" in text or "pet" in text or "dog" in text or "cat" in text:
        return EventCategory.ANIMAL_WILDLIFE
    if "person" in text or "human" in text or "motion" in text:
        return EventCategory.UNKNOWN_VISITOR
    if "ding" in text or "doorbell" in text:
        return EventCategory.UNKNOWN_VISITOR
    return EventCategory.UNKNOWN_VISITOR


class RingNativeAdapter(VideoRecorderPort):
    """Native Ring adapter using the Ring REST API and embedded go2rtc streaming."""

    def __init__(
        self,
        config: RingConfig | None = None,
        temp_manager: Any | None = None,
        stream_manager: NativeStreamManager | None = None,
        client: Any | None = None,
        cameras: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.config = config or RingConfig()
        self.temp_manager = temp_manager
        self.stream_manager = stream_manager
        self._external_client = client
        self.cameras = list(cameras) if cameras is not None else []
        self._ring_client: Any | None = None
        self._auth: Any | None = None
        self._lock = asyncio.Lock()
        self._devices_cache: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Auth / client
    # ------------------------------------------------------------------
    def _token(self) -> str:
        token = getattr(self.config, "refresh_token", None) or getattr(
            self.config, "auth_token", None
        )
        if not token:
            raise RingAuthError(
                "Ring credentials are not configured. Set ring.refresh_token "
                "(the full base64-wrapped token) to use native Ring ingestion."
            )
        return str(token)

    async def _ring(self) -> Any:
        if self._external_client is not None:
            return self._external_client
        if self._ring_client is not None:
            return self._ring_client
        async with self._lock:
            if self._ring_client is not None:
                return self._ring_client
            try:
                from ring_doorbell import Auth, Ring
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise RingAuthError(
                    "ring-doorbell is not installed; install 'ring-doorbell>=0.9.14' "
                    "to use native Ring ingestion"
                ) from exc

            from lookout import __version__

            inner = decode_ring_token(self._token())
            auth = Auth(
                f"lookout/{__version__}",
                token={"refresh_token": inner["rt"]},
                hardware_id=inner.get("hid"),
            )
            await auth.async_refresh_tokens()
            ring = Ring(auth)
            await ring.async_update_devices()
            self._auth = auth
            self._ring_client = ring
            self._persist_refreshed_token(auth)
            return ring

    def _persist_refreshed_token(self, auth: Any) -> None:
        """Re-encode the refreshed token so go2rtc keeps working across restarts."""
        token = getattr(auth, "token", None)
        if not isinstance(token, dict):
            return
        refreshed = token.get("refresh_token")
        if not refreshed:
            return
        try:
            inner = decode_ring_token(self._token())
        except RingAuthError:
            inner = {}
        inner["rt"] = refreshed
        hardware_id = getattr(auth, "hardware_id", None) or inner.get("hid")
        if hardware_id:
            inner["hid"] = hardware_id
        self.config.refresh_token = encode_ring_token(inner)

    async def close(self) -> None:
        """Close the underlying Ring auth session."""
        auth, self._auth = self._auth, None
        self._ring_client = None
        if auth is not None:
            async_close = getattr(auth, "async_close", None)
            if async_close is not None:
                await async_close()

    # ------------------------------------------------------------------
    # Discovery / connection
    # ------------------------------------------------------------------
    @staticmethod
    def _video_devices(ring: Any) -> list[Any]:
        devices = ring.devices()
        video = getattr(devices, "video_devices", [])
        if callable(video):
            video = video()
        return list(video)

    def _map_device(self, device: Any) -> dict[str, Any]:
        api_id = getattr(device, "device_api_id", None)
        device_id = getattr(device, "device_id", None)
        mapped: dict[str, Any] = {
            "id": str(api_id) if api_id is not None else str(device_id),
            "name": getattr(device, "name", None) or str(api_id),
            "device_api_id": api_id,
            "device_id": device_id,
            "model": getattr(device, "model", None),
            "kind": getattr(device, "kind", None),
            "family": getattr(device, "family", None),
            "connection_status": getattr(device, "connection_status", None),
            "native_provider": "ring",
            "stream_source": None,
        }
        try:
            mapped["stream_source"] = self.build_stream_source(mapped)
        except RingStreamSourceError as exc:
            logger.warning("Skipping stream source for Ring device %s: %s", api_id, exc)
        return mapped

    async def test_connection(self) -> dict[str, Any]:
        """Refresh the Ring token and list video devices."""
        try:
            ring = await self._ring()
            devices = [self._map_device(dev) for dev in self._video_devices(ring)]
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.info("Ring connection test failed: %s", exc)
            return {
                "reachable": False,
                "provider": "ring",
                "devices": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "reachable": True,
            "provider": "ring",
            "devices": devices,
            "error": None,
        }

    async def discover_devices(self) -> list[dict[str, Any]]:
        """Discover Ring video devices and cache them for source resolution."""
        ring = await self._ring()
        devices = [self._map_device(dev) for dev in self._video_devices(ring)]
        self._devices_cache = devices
        return devices

    def build_stream_source(self, device: dict[str, Any], snapshot: bool = False) -> str:
        """Build the exact go2rtc ``ring:`` source URL for a device."""
        camera_id = device.get("device_api_id") or device.get("camera_id") or device.get("id")
        device_id = device.get("device_id")
        if camera_id is None or device_id is None:
            raise RingStreamSourceError(
                "Cannot build ring: source: missing device_api_id or device_id"
            )
        token = quote(self._token(), safe="")
        source = f"ring:?camera_id={camera_id}&device_id={device_id}&refresh_token={token}"
        if snapshot:
            source += "&snapshot"
        return source

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

    def _find_cached(self, camera_id: str) -> dict[str, Any] | None:
        for device in self._devices_cache:
            for key in (
                device.get("id"),
                device.get("device_api_id"),
                device.get("device_id"),
                device.get("name"),
            ):
                if key is not None and str(key) == str(camera_id):
                    return device
        return None

    def _resolve_source(self, camera_id: str, snapshot: bool = False) -> str:
        camera = self._camera_config(camera_id)
        if camera is not None:
            stream_url = getattr(camera, "stream_url", None)
            if stream_url and str(stream_url).startswith("ring:"):
                source = str(stream_url)
                if snapshot and "&snapshot" not in source:
                    source += "&snapshot"
                return source
        device = None
        if camera is not None:
            device = self._find_cached(str(camera.native_device_id or ""))
        if device is None:
            device = self._find_cached(camera_id)
        if device is None:
            raise RingStreamSourceError(
                f"No Ring device resolved for camera '{camera_id}'. Run native discovery first."
            )
        return self.build_stream_source(device, snapshot=snapshot)

    async def _resolve_ring_device(self, camera_id: str) -> Any:
        camera = self._camera_config(camera_id)
        ring = await self._ring()
        native_id = getattr(camera, "native_device_id", None) if camera else None
        wanted = {str(camera_id)}
        if native_id:
            wanted.add(str(native_id))
        for device in self._video_devices(ring):
            api_id = getattr(device, "device_api_id", None)
            device_id = getattr(device, "device_id", None)
            name = getattr(device, "name", None)
            identifiers = {str(value) for value in (api_id, device_id, name) if value}
            if wanted & identifiers:
                return device
        raise RingStreamSourceError(
            f"Ring device '{camera_id}' was not found in the account device list"
        )

    # ------------------------------------------------------------------
    # Media capture
    # ------------------------------------------------------------------
    def _require_stream_manager(self) -> NativeStreamManager:
        if self.stream_manager is None:
            raise MediaCaptureError("No NativeStreamManager configured for native Ring capture")
        return self.stream_manager

    def _write_temp(self, suffix: str, content: bytes) -> str:
        if self.temp_manager is not None:
            temp_path = Path(self.temp_manager.create_temp_file(suffix=suffix))
            temp_path.write_bytes(content)
            return str(temp_path)
        fd, temp_name = tempfile.mkstemp(suffix=suffix, prefix="lookout-ring-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return temp_name

    async def capture_snapshot(self, camera_id: str) -> str:
        """Capture a real JPEG frame for a native Ring camera."""
        camera_id = str(camera_id)
        source = self._resolve_source(camera_id, snapshot=True)
        manager = self._require_stream_manager()
        await manager.ensure_stream(camera_id, source)
        content = await manager.frame(camera_id)
        if not content.startswith(_JPEG_MAGIC):
            raise MediaCaptureError(
                f"Snapshot for '{camera_id}' is not a JPEG ({len(content)} bytes)"
            )
        return self._write_temp(".jpg", content)

    async def record_clip(self, camera_id: str, duration_seconds: float = 10.0) -> str:
        """Record a real MP4 clip for a native Ring camera."""
        camera_id = str(camera_id)
        source = self._resolve_source(camera_id, snapshot=False)
        manager = self._require_stream_manager()
        await manager.ensure_stream(camera_id, source)
        # Prime a consumer (frame capture) so on-demand Ring streams negotiate
        # SPS/dimensions before the MP4 request. Swallow priming failures; the
        # subsequent clip call still propagates MediaCaptureError on total failure.
        with contextlib.suppress(MediaCaptureError):
            await manager.frame(camera_id)
        content = await manager.clip(camera_id, duration_seconds)
        if _MP4_FTYP not in content[:64]:
            raise MediaCaptureError(f"Clip for '{camera_id}' is not an MP4 ({len(content)} bytes)")
        return self._write_temp(".mp4", content)

    async def prepare_live_stream(self, camera_id: str) -> None:
        camera_id = str(camera_id)
        source = self._resolve_source(camera_id, snapshot=False)
        manager = self._require_stream_manager()
        await manager.ensure_stream(camera_id, source)
        with contextlib.suppress(MediaCaptureError):
            await manager.frame(camera_id)
        # Register the ffmpeg H.264 transcode stream (no ensure_h264_stream helper on this branch).
        await manager.ensure_stream(f"{camera_id}_h264", f"ffmpeg:{camera_id}#video=h264")
        with contextlib.suppress(MediaCaptureError):
            await manager.frame(f"{camera_id}_h264")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    async def poll_events(
        self, camera_id: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Poll real Ring history events for a camera."""
        camera_id = str(camera_id)
        device = await self._resolve_ring_device(camera_id)
        history = await device.async_history(limit=20)
        if not isinstance(history, Sequence):
            return []
        payloads: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            created = _parse_datetime(item.get("created_at"))
            if since is not None and created is not None and created < _as_utc(since):
                continue
            cv_properties = item.get("cv_properties")
            detection_type = (
                cv_properties.get("detection_type") if isinstance(cv_properties, dict) else None
            )
            kind = item.get("kind")
            event_id = str(item.get("id") or item.get("ding_id") or "")
            payloads.append(
                {
                    "event_id": event_id,
                    "camera_id": camera_id,
                    "timestamp": created.isoformat() if created else None,
                    "kind": kind,
                    "answered": item.get("answered"),
                    "detection_type": detection_type,
                    "metadata": {
                        "provider": "ring",
                        "recording": item.get("recording"),
                        "snapshot_url": item.get("snapshot_url"),
                        "cv_properties": cv_properties,
                    },
                }
            )
        return payloads

    async def ingest_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        """Build a CameraEvent from a real Ring event payload."""
        if not isinstance(payload, dict) or not payload:
            raise ValueError(
                "RingNativeAdapter.ingest_event requires a real event payload; "
                "refusing to invent one"
            )
        camera_id = str(
            payload.get("camera_id")
            or payload.get("device_id")
            or payload.get("device_api_id")
            or ""
        )
        if not camera_id:
            raise ValueError("Ring event payload does not identify a camera")
        event_id = str(payload.get("event_id") or uuid.uuid4())
        timestamp = _parse_datetime(payload.get("timestamp")) or datetime.now(UTC)
        kind = payload.get("kind")
        detection_type = payload.get("detection_type")
        category = _category_from(kind, detection_type)
        summary = f"Ring {kind or 'event'}"
        if detection_type:
            summary += f" ({detection_type})"
        metrics = DetectionMetrics(
            confidence=0.0,
            duration_seconds=float(payload.get("duration_seconds") or 0.0),
        )
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("provider", "ring")
        return CameraEvent(
            id=f"ring-{event_id}",
            camera_id=camera_id,
            timestamp=timestamp,
            category=category,
            threat_level=ThreatLevel.MEDIUM
            if category == EventCategory.UNKNOWN_VISITOR
            else ThreatLevel.LOW,
            summary=summary,
            metrics=metrics,
            metadata=metadata,
        )

    # Compatibility aliases used by the previous scaffold and application code.
    async def ingest(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def ingest_ring_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def capture_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def handle_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def on_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def process_event(self, payload: dict[str, Any] | None = None) -> CameraEvent:
        return await self.ingest_event(payload)

    async def fetch_events(
        self, camera_id: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        return await self.poll_events(camera_id, since)

    async def get_events(
        self, camera_id: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        return await self.poll_events(camera_id, since)
