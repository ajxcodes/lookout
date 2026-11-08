"""In-memory event repository implementation."""

from datetime import datetime

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
