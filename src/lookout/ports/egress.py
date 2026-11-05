"""Egress port definitions for media recording, AI inference, persistence, and notifications."""

from typing import Protocol, runtime_checkable

from lookout.domain.models import (
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)


@runtime_checkable
class VideoRecorderPort(Protocol):
    """Port for video clip capture and snapshot extraction."""

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        """Record video clip for given camera and duration; returns storage URI."""
        ...

    async def capture_snapshot(self, camera_id: str) -> str:
        """Capture single still snapshot image; returns storage URI."""
        ...


@runtime_checkable
class VisionInferencePort(Protocol):
    """Port for visual scene analysis and object detection."""

    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        """Analyze scene image bytes and extract category, metrics, and provenance."""
        ...


@runtime_checkable
class AudioClassifierPort(Protocol):
    """Port for acoustic event detection and audio classification."""

    async def classify_audio(self, audio_bytes: bytes) -> tuple[str, float, InferenceProvenance]:
        """Classify audio sample returning label, confidence, and provenance."""
        ...


@runtime_checkable
class FaceRecognitionPort(Protocol):
    """Port for facial detection and resident identity recognition."""

    async def identify_faces(self, image_bytes: bytes) -> list[tuple[str, float]]:
        """Identify enrolled resident faces; returns list of (resident_id, confidence)."""
        ...


@runtime_checkable
class EventRepositoryPort(Protocol):
    """Port for storing and retrieving camera events."""

    async def save(self, event: CameraEvent) -> None:
        """Persist a camera event."""
        ...

    async def get_by_id(self, event_id: str) -> CameraEvent | None:
        """Retrieve an event by ID."""
        ...

    async def find(
        self,
        camera_id: str | None = None,
        category: EventCategory | None = None,
        min_threat: ThreatLevel | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CameraEvent]:
        """Find events matching search criteria."""
        ...

    async def count(self) -> int:
        """Return total number of saved events."""
        ...


@runtime_checkable
class NotificationPort(Protocol):
    """Port for dispatching alerts to external notification targets."""

    async def send_notification(
        self, event: CameraEvent, target: str, message: str | None = None
    ) -> bool:
        """Dispatch notification for an event to specified target destination."""
        ...
