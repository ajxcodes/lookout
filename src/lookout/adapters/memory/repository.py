"""In-memory event repository implementation."""

from datetime import datetime
from typing import Any

from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel
from lookout.ports.egress import EventRepositoryPort


class InMemoryEventRepository(EventRepositoryPort):
    """In-memory dictionary-backed event repository for testing."""

    def __init__(self) -> None:
        self._events: dict[str, CameraEvent] = {}

    async def save(self, event: CameraEvent) -> None:
        """Persist event in memory dictionary."""
        self._events[event.id] = event

    async def get_by_id(self, event_id: str) -> CameraEvent | None:
        """Retrieve event by ID if present."""
        return self._events.get(event_id)

    async def find(
        self,
        camera_id: str | None = None,
        category: EventCategory | None = None,
        min_threat: ThreatLevel | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        zone_id: str | None = None,
        entity: str | None = None,
    ) -> list[CameraEvent]:
        """Find matching events with filtering and pagination."""
        results: list[CameraEvent] = []
        for event in self._events.values():
            if camera_id is not None and event.camera_id != camera_id:
                continue
            if category is not None and event.category != category:
                continue
            if min_threat is not None and event.threat_level < min_threat:
                continue
            if start_time is not None and event.timestamp < start_time:
                continue
            if end_time is not None and event.timestamp > end_time:
                continue
            if zone_id is not None and event.zone_id != zone_id:
                continue
            if entity is not None:
                e_lower = entity.lower()
                matched = [m.lower() for m in event.matched_entities]
                if (
                    e_lower not in matched
                    and (event.resident_id or "").lower() != e_lower
                    and (event.pet_id or "").lower() != e_lower
                    and (event.vehicle_id or "").lower() != e_lower
                ):
                    continue
            if query is not None:
                q = query.lower()
                tags_str = ""
                raw_tags = event.metadata.get("tags")
                if isinstance(raw_tags, list):
                    tags_str = " ".join(str(t) for t in raw_tags).lower()
                elif isinstance(raw_tags, str):
                    tags_str = raw_tags.lower()
                matched_str = " ".join(m.lower() for m in event.matched_entities)
                if (
                    q not in event.summary.lower()
                    and q not in event.category.value.lower()
                    and q not in tags_str
                    and q not in matched_str
                ):
                    continue
            results.append(event)

        # Sort newest first
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[offset : offset + limit]

    async def count(self) -> int:
        """Return count of stored events."""
        return len(self._events)

    async def delete(self, event_id: str) -> bool:
        """Delete event by ID; returns True if deleted, False if not found."""
        if event_id in self._events:
            del self._events[event_id]
            return True
        return False

    async def migrate_event_media(
        self,
        event_id: str,
        new_media_uri: str | None,
        new_snapshot_uri: str | None = None,
        new_tier: str = "archive",
    ) -> bool:
        """Update media URIs and storage tier for in-memory event."""
        event = self._events.get(event_id)
        if not event:
            return False
        if new_media_uri is not None:
            event.media_uri = new_media_uri
        if new_snapshot_uri is not None:
            event.snapshot_uri = new_snapshot_uri
        event.metadata["storage_tier"] = new_tier
        return True

    async def nullify_media_references(
        self,
        event_id: str,
        tombstone_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Nullify media references and mark as purged."""
        event = self._events.get(event_id)
        if not event:
            return False
        event.media_uri = None
        event.snapshot_uri = None
        event.metadata["media_purged"] = True
        if tombstone_metadata:
            event.metadata.update(tombstone_metadata)
        return True

    async def bookmark_event(self, event_id: str, bookmarked: bool = True) -> bool:
        """Toggle bookmark status on in-memory event."""
        event = self._events.get(event_id)
        if not event:
            return False
        event.bookmarked = bookmarked
        event.metadata["bookmarked"] = bookmarked
        return True

    async def get_eviction_candidates(
        self,
        limit: int = 100,
        preserve_bookmarked: bool = True,
        retention_mode: str = "fifo_oldest_first",
        tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve candidates for eviction from in-memory events."""
        candidates: list[CameraEvent] = []
        for event in self._events.values():
            if not event.media_uri and not event.snapshot_uri:
                continue
            if preserve_bookmarked and (event.bookmarked or event.metadata.get("bookmarked")):
                continue
            event_tier = event.metadata.get("storage_tier", "local")
            if tier == "archive" and event_tier != "archive":
                continue
            if tier == "local" and event_tier == "archive":
                continue
            candidates.append(event)

        if retention_mode == "events_priority":
            candidates.sort(key=lambda e: (e.threat_level, e.timestamp))
        else:
            candidates.sort(key=lambda e: e.timestamp)

        selected = candidates[:limit]
        return [
            {
                "id": e.id,
                "media_uri": e.media_uri,
                "snapshot_uri": e.snapshot_uri,
                "event": e,
                "metadata": e.metadata,
            }
            for e in selected
        ]
