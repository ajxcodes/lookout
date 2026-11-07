"""Server-Sent Events (SSE) broadcast hub for active browser sessions."""

import asyncio
import contextlib
import json
from typing import Any

from lookout.adapters.web.serialization import serialize_event
from lookout.domain.models import CameraEvent


class SSEBroadcastHub:
    """In-memory pub/sub hub for Server-Sent Events dispatch to connected clients."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[str]:
        """Register a new SSE client subscriber queue."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Unregister an SSE client subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)

    async def broadcast(self, message: str) -> None:
        """Broadcast raw message string to all active subscribers."""
        async with self._lock:
            for queue in list(self._subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(message)

    async def broadcast_event(self, event: CameraEvent | dict[str, Any]) -> None:
        """Broadcast a domain CameraEvent or dictionary to all active SSE subscribers."""
        data = serialize_event(event) if isinstance(event, CameraEvent) else event
        payload = json.dumps(data)
        message = f"event: camera_event\ndata: {payload}\n\n"
        await self.broadcast(message)
