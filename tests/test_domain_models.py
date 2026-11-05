"""Unit tests for pure domain models and enums."""

from datetime import UTC, datetime
from uuid import uuid4

from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)


def test_camera_event_creation() -> None:
    """TR-1: Verify CameraEvent instantiates with correct camera identifier."""
    now = datetime.now(UTC)
    event = CameraEvent(
        id=str(uuid4()),
        camera_id="front-door",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident detected at front door",
    )
    assert event.camera_id == "front-door"
    assert event.summary == "Resident detected at front door"
    assert event.metrics.confidence == 0.0
    assert event.provenance == []


def test_threat_level_ordering() -> None:
    """TR-2: Verify ThreatLevel integer comparison and mathematical ordering."""
    assert ThreatLevel.CRITICAL > ThreatLevel.LOW
    assert (
        ThreatLevel.INFO
        < ThreatLevel.LOW
        < ThreatLevel.MEDIUM
        < ThreatLevel.HIGH
        < ThreatLevel.CRITICAL
    )
    assert ThreatLevel.HIGH >= ThreatLevel.MEDIUM
    assert ThreatLevel.INFO == 0
    assert ThreatLevel.CRITICAL == 4


def test_event_category_members() -> None:
    """TR-3: Verify EventCategory enum values and all required members."""
    assert EventCategory.VERIFIED_RESIDENT.value == "verified_resident"
    assert EventCategory.KNOWN_PET.value == "known_pet"
    assert EventCategory.COURIER_DELIVERY.value == "courier_delivery"
    assert EventCategory.FOOD_DELIVERY.value == "food_delivery"
    assert EventCategory.UNKNOWN_VISITOR.value == "unknown_visitor"
    assert EventCategory.ANIMAL_WILDLIFE.value == "animal_wildlife"
    assert EventCategory.VEHICLE.value == "vehicle"
    assert EventCategory.PACKAGE_EVENT.value == "package_event"
    assert EventCategory.SECURITY_INTRUSION.value == "security_intrusion"
    assert EventCategory.ACOUSTIC_ANOMALY.value == "acoustic_anomaly"
    assert EventCategory.FALSE_POSITIVE.value == "false_positive"


def test_camera_event_threat_predicate() -> None:
    """TR-4: Verify CameraEvent.is_threat evaluates correctly across severity levels."""
    now = datetime.now(UTC)
    event = CameraEvent(
        id=str(uuid4()),
        camera_id="driveway",
        timestamp=now,
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.HIGH,
        summary="Unknown intruder at driveway",
    )
    assert event.is_threat() is True

    # Benign events should not be flagged as actionable threats
    benign_event = CameraEvent(
        id=str(uuid4()),
        camera_id="porch",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Package courier",
    )
    assert benign_event.is_threat() is False

    info_event = CameraEvent(
        id=str(uuid4()),
        camera_id="porch",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident Alice",
    )
    assert info_event.is_threat() is False


def test_camera_event_resident_and_pet_predicates() -> None:
    """Verify has_resident and has_pet predicates with IDs and category indicators."""
    now = datetime.now(UTC)
    event_with_id = CameraEvent(
        id=str(uuid4()),
        camera_id="hallway",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident detected",
        resident_id="res-123",
        pet_id="pet-456",
    )
    assert event_with_id.has_resident() is True
    assert event_with_id.has_pet() is True

    event_with_cat_only = CameraEvent(
        id=str(uuid4()),
        camera_id="living-room",
        timestamp=now,
        category=EventCategory.KNOWN_PET,
        threat_level=ThreatLevel.INFO,
        summary="Pet in living room",
    )
    assert event_with_cat_only.has_pet() is True
    assert event_with_cat_only.has_resident() is False

    event_anonymous = CameraEvent(
        id=str(uuid4()),
        camera_id="driveway",
        timestamp=now,
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Unknown visitor",
    )
    assert event_anonymous.has_resident() is False
    assert event_anonymous.has_pet() is False


def test_bounding_box_and_detection_metrics() -> None:
    """Verify BoundingBox and DetectionMetrics instantiation and fields."""
    box = BoundingBox(
        label="person",
        confidence=0.92,
        x_min=0.1,
        y_min=0.2,
        x_max=0.5,
        y_max=0.8,
    )
    assert box.label == "person"
    assert box.confidence == 0.92
    assert box.x_min == 0.1

    metrics = DetectionMetrics(
        confidence=0.92,
        bounding_boxes=[box],
        audio_decibels=65.5,
        duration_seconds=5.0,
        total_latency_ms=45.2,
    )
    assert metrics.confidence == 0.92
    assert len(metrics.bounding_boxes) == 1
    assert metrics.audio_decibels == 65.5
    assert metrics.duration_seconds == 5.0
    assert metrics.total_latency_ms == 45.2


def test_inference_provenance() -> None:
    """Verify InferenceProvenance audit fields and timestamp defaults."""
    prov = InferenceProvenance(
        provider="local-onnx",
        model_name="yolov8n",
        latency_ms=18.4,
        confidence=0.91,
        tokens_used=None,
        raw_output='{"detections": 1}',
    )
    assert prov.provider == "local-onnx"
    assert prov.model_name == "yolov8n"
    assert prov.latency_ms == 18.4
    assert prov.confidence == 0.91
    assert prov.tokens_used is None
    assert prov.raw_output == '{"detections": 1}'
    assert prov.timestamp is not None
