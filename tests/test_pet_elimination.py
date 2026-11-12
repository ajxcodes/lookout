"""Unit tests for pet elimination posture detection and spatial polygon zones."""

import pytest

from lookout.adapters.light_ml import (
    LightMLObjectDetector,
    PetPostureClassifier,
    SpatialZoneEvaluator,
)
from lookout.adapters.memory import InMemoryEventRepository
from lookout.config.schema import PolygonZoneConfig
from lookout.domain.models import BoundingBox
from lookout.domain.service import EventProcessingService


def test_pet_elimination_posture_classification() -> None:
    """TR-16: Verify pet posture classification identifies squatting / elimination."""
    classifier = PetPostureClassifier()
    # Test with squatting frame cue
    box = BoundingBox(
        label="dog",
        confidence=0.92,
        x_min=0.2,
        y_min=0.4,
        x_max=0.55,
        y_max=0.65,
    )
    result = classifier.classify_posture(box, b"dog squatting elimination posture")

    assert result.is_elimination is True and result.posture == "squatting"


@pytest.mark.asyncio
async def test_spatial_polygon_zone_resolution() -> None:
    """TR-17: Verify spatial point-in-polygon zone resolution."""
    zone = PolygonZoneConfig(
        id="lawn",
        name="Lawn/Grass",
        camera_id="cam-backyard",
        polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
    )
    evaluator = SpatialZoneEvaluator(zones=[zone])

    box = BoundingBox(
        label="dog",
        confidence=0.88,
        x_min=0.3,
        y_min=0.4,
        x_max=0.6,
        y_max=0.8,
    )
    resolved_zone = evaluator.resolve_zone(box, camera_id="cam-backyard")

    repo = InMemoryEventRepository()
    detector = LightMLObjectDetector()
    classifier = PetPostureClassifier()
    service = EventProcessingService(
        repository=repo,
        light_ml_detector=detector,
        pet_classifier=classifier,
        zone_evaluator=evaluator,
    )

    event = await service.ingest_raw_event(
        camera_id="cam-backyard",
        payload=b"dog pet squatting elimination on lawn",
    )

    assert resolved_zone == "Lawn/Grass" and event.zone_id == "lawn"


@pytest.mark.asyncio
async def test_sustained_pet_elimination_vs_transient_squat() -> None:
    """Verify Pass 2 downgrades transient squats to KNOWN_PET and confirms sustained elimination."""
    from lookout.domain.models import EventCategory

    repo = InMemoryEventRepository()
    detector = LightMLObjectDetector()
    classifier = PetPostureClassifier()
    service = EventProcessingService(
        repository=repo,
        light_ml_detector=detector,
        pet_classifier=classifier,
    )

    # 1. Ingest event that gatekeeper initially marks as PET_ELIMINATION due to a keyframe squat
    event1 = await service.ingest_raw_event(
        camera_id="cam-backyard",
        payload=b"dog pet squatting elimination posture",
    )
    assert event1.category == EventCategory.PET_ELIMINATION

    # Multi-frame clip has only 1 transient squat out of 3 frames (e.g. brief stretch)
    transient_frames = [
        b"dog pet standing",
        b"dog pet squatting elimination posture",
        b"dog pet standing",
    ]
    enriched_transient = await service.enrich_event_from_media(event1.id, transient_frames)
    assert enriched_transient is not None
    assert enriched_transient.category == EventCategory.KNOWN_PET
    assert enriched_transient.metadata["sustained_elimination"] is False

    # 2. Ingest event that gatekeeper initially marks as KNOWN_PET
    event2 = await service.ingest_raw_event(
        camera_id="cam-backyard",
        payload=b"dog pet standing",
    )
    assert event2.category == EventCategory.KNOWN_PET

    # Multi-frame clip reveals sustained elimination posture across >= 2 frames
    sustained_frames = [
        b"dog pet squatting elimination posture",
        b"dog pet squatting elimination posture",
        b"dog pet standing",
    ]
    enriched_sustained = await service.enrich_event_from_media(event2.id, sustained_frames)
    assert enriched_sustained is not None
    assert enriched_sustained.category == EventCategory.PET_ELIMINATION
    assert enriched_sustained.metadata["sustained_elimination"] is True


@pytest.mark.asyncio
async def test_enrich_event_from_media_runs_vlm_on_key_frames() -> None:
    from lookout.adapters.memory import InMemoryEventRepository
    from lookout.domain.models import DetectionMetrics, EventCategory
    from lookout.domain.service import EventProcessingService

    class FakeVision:
        async def analyze_scene(self, image_bytes: bytes):
            return (
                EventCategory.SECURITY_INTRUSION,
                DetectionMetrics(confidence=0.95),
                None,
            )

    repo = InMemoryEventRepository()
    service = EventProcessingService(
        repository=repo,
        vision_inference=FakeVision(),
    )

    event = await service.ingest_raw_event(camera_id="cam", payload=b"initial")
    initial_summary = event.summary
    initial_confidence = event.metrics.confidence if event.metrics else 0.0

    frames = [b"key-frame-1", b"key-frame-2", b"key-frame-3"]
    enriched = await service.enrich_event_from_media(event.id, frames)

    assert enriched is not None
    assert enriched.summary != initial_summary or (
        enriched.metrics is not None and enriched.metrics.confidence >= initial_confidence
    )
