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
