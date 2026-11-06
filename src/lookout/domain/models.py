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


@dataclass
class DetectionMetrics:
    """Quantitative detection performance and signal metrics."""

    confidence: float = 0.0
    bounding_boxes: list[BoundingBox] = field(default_factory=list)
    audio_decibels: float | None = None
    duration_seconds: float = 0.0
    total_latency_ms: float = 0.0


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

    def is_threat(self) -> bool:
        """Evaluate if the event represents an actionable threat."""
        return self.threat_level >= ThreatLevel.MEDIUM

    def has_resident(self) -> bool:
        """Check if a verified resident is associated with the event."""
        return self.resident_id is not None or self.category == EventCategory.VERIFIED_RESIDENT

    def has_pet(self) -> bool:
        """Check if a known household pet is associated with the event."""
        return self.pet_id is not None or self.category == EventCategory.KNOWN_PET
