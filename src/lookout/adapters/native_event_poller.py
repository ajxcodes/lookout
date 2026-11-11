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
from typing import Any

logger = logging.getLogger(__name__)

_FIRST_TICK_LOOKBACK = timedelta(seconds=60)
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
        interval: float = 15.0,
        *,
        stream_manager: Any | None = None,
        dedup_size: int = _DEFAULT_DEDUP_SIZE,
    ) -> None:
        self.config = config
        self.wyze = wyze
        self.ring = ring
        self.ingest = ingest
        self.on_event = on_event
        self.interval = float(interval)
        self.stream_manager = stream_manager
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._dedup_size = max(1, int(dedup_size))
        self._last_poll: dict[str, datetime] = {}

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

    async def _run(self) -> None:
        now = datetime.now(UTC)
        for camera in self._native_cameras():
            self._last_poll[str(camera.id)] = now - _FIRST_TICK_LOOKBACK
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001 - the poller must never die
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

    def _remember(self, key: tuple[str, str]) -> None:
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self._dedup_size:
            self._seen.popitem(last=False)

    async def _tick(self) -> None:
        for camera in self._native_cameras():
            camera_id = str(camera.id)
            provider = str(getattr(camera, "native_provider", "") or "").lower()
            adapter = self._adapter_for(provider)
            if adapter is None:
                continue
            since = self._last_poll.get(camera_id)
            try:
                events = await adapter.poll_events(camera_id, since)
            except Exception as exc:  # noqa: BLE001 - per-camera isolation
                logger.warning(
                    "Native event poll failed for camera '%s' (%s): %s",
                    camera_id,
                    provider,
                    exc,
                )
                continue
            self._last_poll[camera_id] = datetime.now(UTC)
            for event in events:
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

    async def _handle_event(self, camera_id: str, provider: str, event: dict[str, Any]) -> None:
        image: bytes | None = None
        snapshot_uri: str | None = None
        try:
            image, snapshot_uri = await self._capture_image(camera_id, provider)
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

    async def _capture_image(
        self, camera_id: str, provider: str
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
                    return await manager.frame(camera_id), None
                except Exception as exc:  # noqa: BLE001 - try adapter fallback
                    logger.debug("Stream frame failed for '%s': %s", camera_id, exc)
        adapter = self._adapter_for(provider)
        if adapter is not None and hasattr(adapter, "capture_snapshot"):
            path = await adapter.capture_snapshot(camera_id)
            try:
                return Path(path).read_bytes(), str(path)
            except OSError as exc:
                logger.debug("Could not read snapshot '%s': %s", path, exc)
                return None, str(path)
        return None, None
