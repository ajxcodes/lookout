"""Pure domain models for Lookout camera intelligence platform."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any


class EventCategory(StrEnum):
    """Categorization of detected camera events."""

    VERIFIED_RESIDENT = "verified_resident"
    KNOWN_PET = "known_pet"
    COURIER_DELIVERY = "courier_delivery"
    FOOD_DELIVERY = "food_delivery"
    UNKNOWN_VISITOR = "unknown_visitor"
    ANIMAL_WILDLIFE = "animal_wildlife"
    VEHICLE = "vehicle"
    PACKAGE_EVENT = "package_event"
    SECURITY_INTRUSION = "security_intrusion"
    ACOUSTIC_ANOMALY = "acoustic_anomaly"
    FALSE_POSITIVE = "false_positive"
    PET_ELIMINATION = "pet_elimination"


class ThreatLevel(IntEnum):
    """Graduated threat severity level with natural integer ordering."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class BoundingBox:
    """Normalized object bounding box coordinate and classification."""

    label: str
    confidence: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    entity_id: str | None = None
    entity_name: str | None = None


@dataclass
class DetectionMetrics:
    """Quantitative detection performance and signal metrics."""

    confidence: float = 0.0
    bounding_boxes: list[BoundingBox] = field(default_factory=list)
    audio_decibels: float | None = None
    duration_seconds: float = 0.0
    total_latency_ms: float = 0.0
    tracked_entities: list[str] = field(default_factory=list)
    posture_telemetry: dict[str, Any] | None = None
    zone_id: str | None = None


@dataclass
class InferenceProvenance:
    """Audit trail detailing model execution and provider attribution."""

    provider: str
    model_name: str
    latency_ms: float
    confidence: float
    tokens_used: int | None = None
    raw_output: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class CameraEvent:
    """Aggregate root entity representing a smart camera intelligence event."""

    id: str
    camera_id: str
    timestamp: datetime
    category: EventCategory
    threat_level: ThreatLevel
    summary: str
    metrics: DetectionMetrics = field(default_factory=DetectionMetrics)
    provenance: list[InferenceProvenance] = field(default_factory=list)
    resident_id: str | None = None
    pet_id: str | None = None
    media_uri: str | None = None
    snapshot_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    zone_id: str | None = None
    vehicle_id: str | None = None
    matched_entities: list[str] = field(default_factory=list)
    bookmarked: bool = False

    def is_threat(self) -> bool:
        """Evaluate if the event represents an actionable threat."""
        return self.threat_level >= ThreatLevel.MEDIUM

    def has_resident(self) -> bool:
        """Check if a verified resident is associated with the event."""
        return self.resident_id is not None or self.category == EventCategory.VERIFIED_RESIDENT

    def has_pet(self) -> bool:
        """Check if a known household pet is associated with the event."""
        return self.pet_id is not None or self.category == EventCategory.KNOWN_PET

    def has_vehicle(self) -> bool:
        """Check if a household or detected vehicle is associated with the event."""
        return (
            self.vehicle_id is not None
            or self.category == EventCategory.VEHICLE
            or any(
                any(c in e.lower() for c in ("vehicle", "car", "civic"))
                for e in self.matched_entities
            )
        )

    def is_pet_elimination(self) -> bool:
        """Check if the event represents pet elimination posture."""
        return self.category == EventCategory.PET_ELIMINATION


@dataclass
class StorageStatus:
    """Storage usage metrics, quota limits, and volume telemetry for local and archive tiers."""

    volume_total_bytes: int
    volume_used_bytes: int
    volume_available_bytes: int
    volume_used_percent: float
    media_total_bytes: int
    media_breakdown: dict[str, int]
    max_storage_bytes: int
    max_storage_gb: float
    max_storage_percent: float
    media_quota_used_percent: float
    warning_threshold_percent: float = 90.0
    is_warning: bool = False
    retention_mode: str = "fifo_oldest_first"
    preserve_bookmarked: bool = True
    cleanup_interval_seconds: float = 300.0
    retention_runway_hours: float | None = None
    archive_enabled: bool = False
    archive_path: str | None = None
    archive_available: bool = False
    archive_volume_total_bytes: int = 0
    archive_volume_used_bytes: int = 0
    archive_volume_available_bytes: int = 0
    archive_volume_used_percent: float = 0.0
    archive_media_total_bytes: int = 0
    archive_media_breakdown: dict[str, int] = field(default_factory=dict)
    archive_max_storage_bytes: int = 0
    archive_max_storage_gb: float | None = None
    archive_max_storage_percent: float | None = None
    archive_quota_used_percent: float = 0.0
    archive_is_warning: bool = False


@dataclass
class EvictionResult:
    """Outcome of a FIFO storage eviction run."""

    files_migrated: int = 0
    bytes_migrated: int = 0
    files_deleted: int = 0
    bytes_freed: int = 0
    events_pruned: int = 0
