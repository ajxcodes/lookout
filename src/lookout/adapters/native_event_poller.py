"""Poll native Wyze/Ring events and ingest them into the Lookout pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from lookout.adapters.native_stream_manager import redact_error

logger = logging.getLogger(__name__)

_FIRST_TICK_LOOKBACK = timedelta(seconds=60)


class _AwaitableDict(dict[str, Any]):
    """A dict that is also awaitable, so ``poller.status()`` works sync and async."""

    def __await__(self) -> Any:
        async def _coro() -> _AwaitableDict:
            return self

        return _coro().__await__()


_DEFAULT_DEDUP_SIZE = 500


class NativeEventPoller:
    """Periodically poll native provider events and feed the ingestion pipeline.

    The poller is deliberately resilient: adapter or capture failures are logged
    and the loop keeps running. Events are deduplicated by ``(provider, event_id)``
    using a bounded store so a restart cannot replay the full history.
    """

    def __init__(
        self,
        config: Any | None,
        wyze: Any | None,
        ring: Any | None,
        ingest: Callable[[str, dict[str, Any]], Awaitable[Any]],
        on_event: Callable[[Any], Awaitable[Any]] | None = None,
        on_clip_ready: Callable[[Any], Awaitable[Any]] | None = None,
        interval: float = 15.0,
        *,
        stream_manager: Any | None = None,
        dedup_size: int = _DEFAULT_DEDUP_SIZE,
        record_clip: bool = True,
        clip_duration: float = 10.0,
        storage_base_path: str | Path | None = None,
        failure_backoff_seconds: float = 0.0,
        max_backoff_seconds: float = 900.0,
    ) -> None:
        self.config = config
        self.wyze = wyze
        self.ring = ring
        self.ingest = ingest
        self.on_event = on_event
        self.on_clip_ready = on_clip_ready
        self.interval = float(interval)
        self.failure_backoff_seconds = max(0.0, float(failure_backoff_seconds))
        self.max_backoff_seconds = max(0.0, float(max_backoff_seconds))
        self.stream_manager = stream_manager
        self.record_clip = bool(record_clip)
        self.clip_duration = float(clip_duration)
        self.storage_base_path = Path(storage_base_path) if storage_base_path is not None else None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._dedup_size = max(1, int(dedup_size))
        self._last_poll: dict[str, datetime] = {}
        # #64: global failure counter + per-provider pause control (logout flow).
        self.consecutive_failures = 0
        self.paused_providers: set[str] = set()
        # Collector: per-camera health tracking (last poll/error/event counts).
        self._health: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the polling loop as a background task (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the polling loop and wait for it to finish."""
        self._stop_event.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Health / status (both #64 global health and collector per-camera health)
    # ------------------------------------------------------------------
    @property
    def health(self) -> dict[str, Any]:
        """Read-only poller health snapshot (no credential material)."""
        last_poll = max(self._last_poll.values()) if self._last_poll else None
        return {
            "running": self.running,
            "consecutive_failures": self.consecutive_failures,
            "last_poll": last_poll.isoformat() if last_poll is not None else None,
            "paused_providers": sorted(self.paused_providers),
        }

    def pause(self, provider: str) -> None:
        """Pause native polling for a provider (used by logout)."""
        self.paused_providers.add(str(provider).lower())

    def resume(self, provider: str) -> None:
        """Resume native polling for a provider (used by a successful login)."""
        self.paused_providers.discard(str(provider).lower())

    def _ensure_health(self, camera_id: str) -> dict[str, Any]:
        health = self._health.get(camera_id)
        if health is None:
            health = {
                "last_poll_at": None,
                "last_success_at": None,
                "last_event_count": 0,
                "last_error": None,
                "consecutive_failures": 0,
                "next_retry_at": None,
                "retry_after_seconds": 0.0,
            }
            self._health[camera_id] = health
        return health

    @staticmethod
    def _redact_error(text: Any) -> str:
        """Redact credential-like values from an adapter error."""
        return redact_error(text)

    def _serialize_health(self) -> dict[str, Any]:
        return {key: dict(value) for key, value in self._health.items()}

    def status(self) -> Any:
        serialized = self._serialize_health()
        base: dict[str, Any] = {
            "running": self.running,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "last_event_count": self.last_event_count,
            "cameras": serialized,
            "health": serialized,
            "per_camera": serialized,
            "event_poller_running": self.running,
        }
        # Also expose per-camera health at the top level for convenience.
        for camera_id, health in serialized.items():
            base[camera_id] = dict(health)
        return _AwaitableDict(base)

    @property
    def last_error(self) -> Any:
        errors = [
            health.get("last_error") for health in self._health.values() if health.get("last_error")
        ]
        return errors[-1] if errors else None

    @property
    def last_success(self) -> Any:
        successes: list[datetime] = [
            cast(datetime, health.get("last_success_at"))
            for health in self._health.values()
            if health.get("last_success_at") is not None
        ]
        return max(successes) if successes else None

    @property
    def last_event_count(self) -> int:
        return sum(int(health.get("last_event_count") or 0) for health in self._health.values())

    async def _run(self) -> None:
        now = datetime.now(UTC)
        for camera in self._native_cameras():
            self._last_poll[str(camera.id)] = now - _FIRST_TICK_LOOKBACK
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001 - the poller must never die
                self.consecutive_failures += 1
                logger.warning("Native event poll tick failed: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _native_cameras(self) -> list[Any]:
        cameras = getattr(self.config, "cameras", None) or []
        return [
            camera
            for camera in cameras
            if getattr(camera, "enabled", True)
            and str(getattr(camera, "native_provider", "") or "").lower() in ("wyze", "ring")
        ]

    def _adapter_for(self, provider: str) -> Any | None:
        if provider == "wyze":
            return self.wyze
        if provider == "ring":
            return self.ring
        return None

    def _manager_for(self, provider: str) -> Any | None:
        if self.stream_manager is not None:
            return self.stream_manager
        adapter = self._adapter_for(provider)
        return getattr(adapter, "stream_manager", None)

    def _storage_base_path(self) -> Path | None:
        if self.storage_base_path is not None:
            return self.storage_base_path
        storage = getattr(self.config, "storage", None)
        base_path = getattr(storage, "base_path", None)
        if base_path:
            return Path(base_path)
        return None

    def _persist_snapshot_bytes(self, camera_id: str, kind: str, content: bytes) -> str | None:
        base_path = self._storage_base_path()
        if base_path is None or not content:
            return None
        try:
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            safe_kind = (kind or "motion").replace("/", "_")
            name = f"{camera_id}_{safe_kind}_{stamp}.jpg"
            event_snap_dir = base_path / "snapshots" / "events"
            event_snap_dir.mkdir(parents=True, exist_ok=True)
            (event_snap_dir / name).write_bytes(content)

            cam_snap_dir = base_path / "snapshots"
            cam_snap_dir.mkdir(parents=True, exist_ok=True)
            (cam_snap_dir / f"{camera_id}.jpg").write_bytes(content)
            return f"/media/events/{name}"
        except OSError as exc:
            logger.debug("Could not persist snapshot for '%s': %s", camera_id, exc)
            return None

    def _remember(self, key: tuple[str, str]) -> None:
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self._dedup_size:
            self._seen.popitem(last=False)

    async def _tick(self) -> None:
        had_failure = False
        now = datetime.now(UTC)
        for camera in self._native_cameras():
            camera_id = str(camera.id)
            provider = str(getattr(camera, "native_provider", "") or "").lower()
            if provider in self.paused_providers:
                continue
            adapter = self._adapter_for(provider)
            if adapter is None:
                continue
            health = self._ensure_health(camera_id)
            next_retry_at = health.get("next_retry_at")
            if next_retry_at is not None and now < next_retry_at:
                # Inside the per-camera cooldown: skip without another log line.
                continue
            health["last_poll_at"] = now
            since = self._last_poll.get(camera_id)
            if since is not None:
                since = since - timedelta(seconds=2)
            try:
                events = await adapter.poll_events(camera_id, since)
            except Exception as exc:  # noqa: BLE001 - per-camera isolation
                had_failure = True
                self.consecutive_failures += 1
                failures = int(health.get("consecutive_failures") or 0) + 1
                health["consecutive_failures"] = failures
                redacted = self._redact_error(str(exc))
                health["last_error"] = redacted
                if self.failure_backoff_seconds > 0:
                    delay = min(
                        self.max_backoff_seconds,
                        self.failure_backoff_seconds * (2 ** (failures - 1)),
                    )
                    health["next_retry_at"] = datetime.now(UTC) + timedelta(seconds=delay)
                    health["retry_after_seconds"] = float(delay)
                    if not health.get("_backoff_logged"):
                        logger.warning(
                            "Native event poll failed for camera '%s' (%s): %s; backing off %.1fs",
                            camera_id,
                            provider,
                            redacted,
                            delay,
                        )
                        health["_backoff_logged"] = True
                else:
                    logger.warning(
                        "Native event poll failed for camera '%s' (%s): %s",
                        camera_id,
                        provider,
                        redacted,
                    )
                continue
            returned = events if isinstance(events, list) else list(events or [])
            count = len(returned)
            logger.debug(
                "Native event poll for camera '%s' (%s) returned %d event(s)",
                camera_id,
                provider,
                count,
            )
            health["last_success_at"] = datetime.now(UTC)
            health["last_event_count"] = count
            health["last_error"] = None
            health["consecutive_failures"] = 0
            health["next_retry_at"] = None
            health["retry_after_seconds"] = 0.0
            health["_backoff_logged"] = False
            self._last_poll[camera_id] = datetime.now(UTC)
            for event in returned:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("event_id") or "")
                if not event_id:
                    event_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{provider}:{event.get('timestamp')}:{event.get('kind')}",
                        )
                    )
                key = (provider, event_id)
                if key in self._seen:
                    continue
                self._remember(key)
                await self._handle_event(camera_id, provider, event)
        if not had_failure:
            self.consecutive_failures = 0

    async def _handle_event(self, camera_id: str, provider: str, event: dict[str, Any]) -> None:
        image: bytes | None = None
        snapshot_uri: str | None = None
        kind = str(event.get("kind") or event.get("alarm_type") or "motion")
        try:
            image, snapshot_uri = await self._capture_image(camera_id, provider, kind=kind)
        except Exception as exc:  # noqa: BLE001 - metadata-only ingest is acceptable
            logger.info(
                "Snapshot capture failed for native event on '%s' (%s): %s",
                camera_id,
                provider,
                exc,
            )
        payload: dict[str, Any] = {"metadata": event}
        if image is not None:
            payload["image"] = image
        if snapshot_uri is not None:
            payload["snapshot_uri"] = snapshot_uri
        try:
            result = await self.ingest(camera_id, payload)
        except Exception as exc:  # noqa: BLE001 - keep polling other events
            logger.warning(
                "Native event ingest failed for camera '%s' event '%s': %s",
                camera_id,
                event.get("event_id"),
                exc,
            )
            return
        if self.on_event is not None:
            try:
                await self.on_event(result)
            except Exception as exc:  # noqa: BLE001 - broadcast is best-effort
                logger.warning("Native event broadcast failed for '%s': %s", camera_id, exc)
        if result is not None and self.record_clip and self.on_clip_ready is not None:
            asyncio.create_task(
                self._record_clip_background(
                    camera_id, provider, result, duration=self.clip_duration
                )
            )

    async def _capture_image(
        self, camera_id: str, provider: str, *, kind: str = "motion"
    ) -> tuple[bytes | None, str | None]:
        """Best-effort snapshot capture for an event.

        Prefers an already-registered go2rtc stream, then falls back to the
        adapter's own capture path (which registers the stream first).
        """
        manager = self._manager_for(provider)
        if manager is not None:
            streams = getattr(manager, "streams", None)
            if streams is not None and camera_id in streams:
                try:
                    content = await manager.frame(camera_id)
                    uri = self._persist_snapshot_bytes(camera_id, kind, content)
                    return content, uri
                except Exception as exc:  # noqa: BLE001 - try adapter fallback
                    logger.debug("Stream frame failed for '%s': %s", camera_id, exc)
        adapter = self._adapter_for(provider)
        if adapter is not None and hasattr(adapter, "capture_snapshot"):
            path = await adapter.capture_snapshot(camera_id)
            try:
                content = Path(path).read_bytes()
                uri = self._persist_snapshot_bytes(camera_id, kind, content)
                with contextlib.suppress(OSError):
                    if uri is not None:
                        Path(path).unlink(missing_ok=True)
                return content, uri or str(path)
            except OSError as exc:
                logger.debug("Could not read snapshot '%s': %s", path, exc)
                return None, str(path)
        return None, None

    async def _record_clip_background(
        self, camera_id: str, provider: str, event: Any, duration: float = 10.0
    ) -> None:
        """Record a background MP4 clip (Pass 2) and invoke on_clip_ready."""
        event_id = (
            event.get("id") if isinstance(event, dict) else getattr(event, "id", None)
        ) or str(uuid.uuid4())
        clip_bytes: bytes | None = None
        temp_clip_path: Path | None = None
        clip_path: Path | None = None

        try:
            manager = self._manager_for(provider)
            adapter = self._adapter_for(provider)

            if adapter is not None and hasattr(adapter, "record_clip"):
                path_str = await adapter.record_clip(camera_id, duration)
                temp_clip_path = Path(path_str)
                if temp_clip_path.is_file():
                    clip_bytes = temp_clip_path.read_bytes()
            elif manager is not None and hasattr(manager, "clip"):
                clip_bytes = await manager.clip(camera_id, duration)
            elif manager is not None and hasattr(manager, "record_clip"):
                path_str = await manager.record_clip(camera_id, duration)
                temp_clip_path = Path(path_str)
                if temp_clip_path.is_file():
                    clip_bytes = temp_clip_path.read_bytes()

            if not clip_bytes:
                return

            base_path = self._storage_base_path() or Path("./data")
            events_dir = base_path / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            name = f"{camera_id}_{event_id}.mp4"
            clip_path = events_dir / name
            clip_path.write_bytes(clip_bytes)

            media_uri = f"/media/events/{name}"
            if isinstance(event, dict):
                event["media_uri"] = media_uri
            else:
                event.media_uri = media_uri

            if self.on_clip_ready is not None:
                await self.on_clip_ready(event)
        except Exception as exc:  # noqa: BLE001 - background clip must never crash
            logger.warning(
                "Background native clip recording failed for '%s' (%s): %s",
                camera_id,
                provider,
                exc,
            )
        finally:
            if temp_clip_path is not None and temp_clip_path != clip_path:
                with contextlib.suppress(OSError):
                    temp_clip_path.unlink(missing_ok=True)
