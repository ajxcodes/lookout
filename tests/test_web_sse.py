"""Integration tests for Server-Sent Events (SSE) live streaming."""

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


def test_events_sse_stream_receives_broadcast() -> None:
    """TR-9: Verify SSE endpoint streams real-time broadcast events to connected client."""
    repo = InMemoryEventRepository()
    app = create_app(repository=repo)

    event = CameraEvent(
        id="event-broadcast-1",
        camera_id="front-porch",
        timestamp=datetime.now(UTC),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Broadcast test event",
    )

    with TestClient(app) as client, client.stream("GET", "/api/events/stream") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Broadcast event while stream connection is active
        asyncio.run(app.state.broadcast_hub.broadcast_event(event))

        found = False
        for line in response.iter_lines():
            if "event-broadcast-1" in line:
                event_line = line
                assert "event-broadcast-1" in event_line
                found = True
                break

        assert found is True
