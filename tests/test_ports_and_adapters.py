"""Unit tests for ports, in-memory mock adapters, and domain orchestrator service."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
)
from lookout.domain.models import (
    CameraEvent,
    EventCategory,
    ThreatLevel,
)
from lookout.domain.service import EventProcessingService


@pytest.mark.asyncio
async def test_in_memory_repository_save_and_retrieve() -> None:
    """TR-8: Verify InMemoryEventRepository saves and retrieves CameraEvent by ID."""
    repo = InMemoryEventRepository()
    now = datetime.now(UTC)
    event = CameraEvent(
        id=str(uuid4()),
        camera_id="cam-backyard",
        timestamp=now,
        category=EventCategory.ANIMAL_WILDLIFE,
        threat_level=ThreatLevel.LOW,
        summary="Raccoon detected",
    )

    await repo.save(event)
    assert await repo.count() == 1

    retrieved = await repo.get_by_id(event.id)
    assert retrieved is not None
    assert retrieved.id == event.id


@pytest.mark.asyncio
async def test_in_memory_repository_find_and_filter() -> None:
    """Verify repository filtering by camera, category, threat level, and pagination."""
    repo = InMemoryEventRepository()
    now = datetime.now(UTC)

    e1 = CameraEvent(
        id="e1",
        camera_id="cam-1",
        timestamp=now,
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Visitor",
    )
    e2 = CameraEvent(
        id="e2",
        camera_id="cam-1",
        timestamp=now,
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.CRITICAL,
        summary="Intruder",
    )
    e3 = CameraEvent(
        id="e3",
        camera_id="cam-2",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident",
    )

    await repo.save(e1)
    await repo.save(e2)
    await repo.save(e3)

    # Filter by camera
    cam1_events = await repo.find(camera_id="cam-1")
    assert len(cam1_events) == 2

    # Filter by category
    resident_events = await repo.find(category=EventCategory.VERIFIED_RESIDENT)
    assert len(resident_events) == 1
    assert resident_events[0].id == "e3"

    # Filter by minimum threat level
    threat_events = await repo.find(min_threat=ThreatLevel.MEDIUM)
    assert len(threat_events) == 2

    # Test pagination
    paginated = await repo.find(camera_id="cam-1", limit=1, offset=0)
    assert len(paginated) == 1

    # Missing ID returns None
    missing = await repo.get_by_id("missing-id")
    assert missing is None


@pytest.mark.asyncio
async def test_mock_notification_adapter_records_dispatch() -> None:
    """TR-9: Verify MockNotificationAdapter records dispatched notification."""
    adapter = MockNotificationAdapter()
    now = datetime.now(UTC)
    event = CameraEvent(
        id=str(uuid4()),
        camera_id="driveway",
        timestamp=now,
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.HIGH,
        summary="Security alarm",
    )

    success = await adapter.send_notification(event, target="webhook-alerts")
    assert success is True
    assert len(adapter.sent_notifications) == 1
    assert adapter.sent_notifications[0]["target"] == "webhook-alerts"
    assert adapter.sent_notifications[0]["event"].id == event.id


@pytest.mark.asyncio
async def test_event_processing_service_pipeline() -> None:
    """TR-10: Verify end-to-end pipeline processes raw event and recognizes resident."""
    repo = InMemoryEventRepository()
    notifier = MockNotificationAdapter()
    recorder = MockVideoRecorderAdapter()
    vision = MockVisionInferenceAdapter(category=EventCategory.UNKNOWN_VISITOR)
    face = MockFaceRecognitionAdapter(recognized_faces=[("resident_alice", 0.98)])

    service = EventProcessingService(
        repository=repo,
        notification=notifier,
        video_recorder=recorder,
        vision_inference=vision,
        face_recognition=face,
        notification_threshold=ThreatLevel.LOW,
    )

    raw_payload = {"image": b"fake-resident-jpeg-bytes", "metadata": {"trigger": "motion"}}
    processed = await service.ingest_raw_event("front-porch", raw_payload)

    assert processed.category == EventCategory.VERIFIED_RESIDENT
    assert processed.threat_level == ThreatLevel.INFO
    assert processed.resident_id == "resident_alice"
    assert processed.snapshot_uri is not None
    assert processed.media_uri is not None
    # Event should be saved in repository
    assert await repo.count() == 1


@pytest.mark.asyncio
async def test_event_processing_service_threat_dispatch() -> None:
    """Verify threat events trigger notification dispatch according to threshold."""
    repo = InMemoryEventRepository()
    notifier = MockNotificationAdapter()
    recorder = MockVideoRecorderAdapter()
    vision = MockVisionInferenceAdapter(category=EventCategory.UNKNOWN_VISITOR)
    # No faces recognized -> remains UNKNOWN_VISITOR -> threat level MEDIUM
    face = MockFaceRecognitionAdapter(recognized_faces=[])

    service = EventProcessingService(
        repository=repo,
        notification=notifier,
        video_recorder=recorder,
        vision_inference=vision,
        face_recognition=face,
        notification_threshold=ThreatLevel.MEDIUM,
        notification_target="mobile-push",
    )

    processed = await service.ingest_raw_event("side-gate", b"raw-image-bytes")
    assert processed.category == EventCategory.UNKNOWN_VISITOR
    assert processed.threat_level == ThreatLevel.MEDIUM
    assert len(notifier.sent_notifications) == 1
    assert notifier.sent_notifications[0]["target"] == "mobile-push"


@pytest.mark.asyncio
async def test_event_processing_service_acoustic_anomaly() -> None:
    """Verify acoustic anomaly audio classification elevates threat level to HIGH."""
    repo = InMemoryEventRepository()
    notifier = MockNotificationAdapter()
    audio = MockAudioClassifierAdapter(label="glass_break", confidence=0.94)

    service = EventProcessingService(
        repository=repo,
        notification=notifier,
        audio_classifier=audio,
        notification_threshold=ThreatLevel.MEDIUM,
    )

    payload = {"audio": b"acoustic-raw-pcm"}
    processed = await service.ingest_raw_event("living-room", payload)
    assert processed.category == EventCategory.ACOUSTIC_ANOMALY
    assert processed.threat_level == ThreatLevel.HIGH
    assert len(notifier.sent_notifications) == 1


@pytest.mark.asyncio
async def test_mock_adapters_standalone() -> None:
    """Verify standalone methods of all mock adapters."""
    vision = MockVisionInferenceAdapter()
    cat, metrics, prov = await vision.analyze_scene(b"test")
    assert cat == EventCategory.UNKNOWN_VISITOR
    assert metrics.confidence == 0.88
    assert prov.provider == "mock"

    audio = MockAudioClassifierAdapter()
    label, conf, audio_prov = await audio.classify_audio(b"audio")
    assert label == "speech"
    assert conf == 0.85

    recorder = MockVideoRecorderAdapter(base_storage_uri="/var/lookout")
    clip = await recorder.record_clip("cam-1", 5.0)
    assert clip == "/var/lookout/recordings/cam-1_5.0s.mp4"
    snapshot = await recorder.capture_snapshot("cam-1")
    assert snapshot == "/var/lookout/snapshots/cam-1.jpg"


def test_evaluate_threat_level_all_categories() -> None:
    """Verify threat level evaluation covering all event categories."""
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.VERIFIED_RESIDENT)
        == ThreatLevel.INFO
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.KNOWN_PET) == ThreatLevel.INFO
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.FALSE_POSITIVE)
        == ThreatLevel.INFO
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.COURIER_DELIVERY)
        == ThreatLevel.LOW
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.FOOD_DELIVERY)
        == ThreatLevel.LOW
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.PACKAGE_EVENT)
        == ThreatLevel.LOW
    )
    assert EventProcessingService._evaluate_threat_level(EventCategory.VEHICLE) == ThreatLevel.LOW
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.ANIMAL_WILDLIFE)
        == ThreatLevel.LOW
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.UNKNOWN_VISITOR)
        == ThreatLevel.MEDIUM
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.ACOUSTIC_ANOMALY)
        == ThreatLevel.HIGH
    )
    assert (
        EventProcessingService._evaluate_threat_level(EventCategory.SECURITY_INTRUSION)
        == ThreatLevel.CRITICAL
    )
    assert EventProcessingService._evaluate_threat_level("custom") == ThreatLevel.LOW  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_event_processing_service_payload_variations() -> None:
    """Verify payload parsing handles empty dict and non-dict/non-bytes payloads."""
    repo = InMemoryEventRepository()
    service = EventProcessingService(repository=repo)

    # Empty dict payload
    event = await service.ingest_raw_event("cam-1", {})
    assert event.camera_id == "cam-1"
    assert event.metrics.confidence == 0.0

    # Payload as arbitrary type (neither dict nor bytes)
    event2 = await service.ingest_raw_event("cam-2", 12345)  # type: ignore[arg-type]
    assert event2.camera_id == "cam-2"
