"""REST API endpoints for event queries, single event lookup, deletion, and SSE streaming."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from lookout.adapters.web.broadcast import SSEBroadcastHub
from lookout.adapters.web.serialization import serialize_event
from lookout.domain.models import EventCategory, ThreatLevel
from lookout.ports.egress import EventRepositoryPort

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.get("/events")
async def list_events(
    request: Request,
    camera_id: str | None = Query(None, description="Filter by camera ID"),
    category: str | None = Query(None, description="Filter by event category"),
    min_threat: int | None = Query(None, description="Minimum threat level (0-4)"),
    start_time: str | None = Query(None, description="ISO format start datetime"),
    end_time: str | None = Query(None, description="ISO format end datetime"),
    query: str | None = Query(None, description="Full text search query"),
    zone_id: str | None = Query(None, description="Spatial zone identifier"),
    entity: str | None = Query(None, description="Entity name or identifier"),
    limit: int = Query(50, ge=1, le=500, description="Page size limit"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> dict[str, Any]:
    """Retrieve filtered, paginated camera events."""
    repo: EventRepositoryPort = request.app.state.repository

    cat_enum: EventCategory | None = None
    if category:
        try:
            cat_enum = EventCategory(category.lower())
        except ValueError:
            logger.warning("Unrecognized category filter: %s", category)

    threat_enum: ThreatLevel | None = None
    if min_threat is not None:
        try:
            threat_enum = ThreatLevel(min_threat)
        except ValueError:
            logger.warning("Unrecognized min_threat filter: %s", min_threat)

    dt_start: datetime | None = None
    if start_time:
        with contextlib.suppress(ValueError):
            dt_start = datetime.fromisoformat(start_time)

    dt_end: datetime | None = None
    if end_time:
        with contextlib.suppress(ValueError):
            dt_end = datetime.fromisoformat(end_time)

    events = await repo.find(
        camera_id=camera_id,
        category=cat_enum,
        min_threat=threat_enum,
        start_time=dt_start,
        end_time=dt_end,
        query=query,
        limit=limit,
        offset=offset,
        zone_id=zone_id,
        entity=entity,
    )
    total = await repo.count()
    return {
        "events": [serialize_event(e) for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/events/stream")
async def stream_events(request: Request) -> StreamingResponse:
    """Server-Sent Events endpoint streaming real-time live events to web clients."""
    hub: SSEBroadcastHub = request.app.state.broadcast_hub
    queue = await hub.subscribe()

    async def sse_generator() -> AsyncGenerator[str, None]:
        try:
            yield ": connected\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except TimeoutError:
                    yield ": ping\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            await hub.unsubscribe(queue)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/{event_id}")
async def get_event(event_id: str, request: Request) -> dict[str, Any]:
    """Retrieve full details of a single event by ID."""
    repo: EventRepositoryPort = request.app.state.repository
    event = await repo.get_by_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return serialize_event(event)


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, request: Request) -> dict[str, Any]:
    """Delete an event by ID."""
    repo: EventRepositoryPort = request.app.state.repository
    deleted = await repo.delete(event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return {"deleted": True, "event_id": event_id}
