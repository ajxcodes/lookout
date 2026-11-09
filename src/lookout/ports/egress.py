"""Egress port definitions for media recording, AI inference, persistence, and notifications."""

from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from lookout.domain.models import (
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    EvictionResult,
    InferenceProvenance,
    StorageStatus,
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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        zone_id: str | None = None,
        entity: str | None = None,
    ) -> list[CameraEvent]:
        """Find events matching search criteria."""
        ...

    async def count(self) -> int:
        """Return total number of saved events."""
        ...

    async def delete(self, event_id: str) -> bool:
        """Delete an event by ID; returns True if deleted, False if not found."""
        ...


@runtime_checkable
class NotificationPort(Protocol):
    """Port for dispatching alerts to external notification targets."""

    async def send_notification(
        self, event: CameraEvent, target: str, message: str | None = None
    ) -> bool:
        """Dispatch notification for an event to specified target destination."""
        ...


@runtime_checkable
class MediaTranscoderPort(Protocol):
    """Port for video normalization, preview generation, and thumbnail extraction."""

    async def normalize_mp4(self, input_path: str, output_path: str | None = None) -> str:
        """Normalize or remux video into standard faststart MP4; returns output path."""
        ...

    async def generate_preview_gif(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Generate animated GIF push preview; returns output path."""
        ...

    async def generate_preview_webp(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Generate animated WebP push preview; returns output path."""
        ...

    async def generate_thumbnails(
        self,
        input_path: str,
        output_dir: str | None = None,
        count: int = 5,
        width: int = 320,
    ) -> list[str]:
        """Extract scrubbed thumbnail stills across video duration; returns list of image paths."""
        ...


@runtime_checkable
class StorageServicePort(Protocol):
    """Port for storage quota monitoring, telemetry reporting, and FIFO eviction."""

    def get_storage_status(self) -> StorageStatus:
        """Calculate and return current dual-tier storage status."""
        ...

    async def execute_fifo_eviction(self, target_bytes: int | None = None) -> EvictionResult:
        """Execute FIFO eviction across tiers down to target bytes or low-watermark buffer."""
        ...

    def start(self) -> None:
        """Start background cleaner daemon."""
        ...

    async def stop(self) -> None:
        """Stop background cleaner daemon."""
        ...


@runtime_checkable
class MediaArchivalPort(Protocol):
    """Port for external media archival hooks before permanent file deletion."""

    async def on_pre_evict(self, event: CameraEvent, file_paths: list[Path]) -> None:
        """Hook invoked before permanent media file unlinking."""
        ...
