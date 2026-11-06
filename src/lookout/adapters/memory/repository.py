"""In-memory event repository implementation."""

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
        limit: int = 50,
        offset: int = 0,
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
            results.append(event)

        # Sort newest first
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[offset : offset + limit]

    async def count(self) -> int:
        """Return count of stored events."""
        return len(self._events)
