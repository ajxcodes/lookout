"""Ingress port definitions for incoming events and API queries."""

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@runtime_checkable
class EventIngressPort(Protocol):
    """Port for receiving and processing raw camera events."""

    async def ingest_raw_event(
        self, camera_id: str, payload: bytes | dict[str, Any]
    ) -> CameraEvent:
        """Parse raw incoming trigger payload into a domain CameraEvent."""
        ...

    async def process_event(self, event: CameraEvent) -> CameraEvent:
        """Process and orchestrate an event through analysis, evaluation, and persistence."""
        ...


@runtime_checkable
class ViewerApiPort(Protocol):
    """Port for viewer querying and timeline retrieval."""

    async def get_event(self, event_id: str) -> CameraEvent | None:
        """Retrieve a single event by unique identifier."""
        ...

    async def list_events(
        self,
        camera_id: str | None = None,
        category: EventCategory | None = None,
        min_threat: ThreatLevel | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CameraEvent]:
        """List events filtered by camera, category, or threat level."""
        ...

    async def get_timeline(self, start_time: datetime, end_time: datetime) -> list[CameraEvent]:
        """Retrieve events occurring within a specified time interval."""
        ...
