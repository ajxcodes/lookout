"""Unit tests for Light ML micro-footprint object detection and illumination pre-filter."""

import pytest

from lookout.adapters.light_ml import IlluminationFilter, LightMLObjectDetector
from lookout.adapters.memory import InMemoryEventRepository
from lookout.domain.models import DetectionMetrics, EventCategory
from lookout.domain.service import EventProcessingService


def test_light_ml_cpu_detection_latency_under_40ms() -> None:
    """TR-13: Verify Light ML CPU detection executes in under 40ms."""
    detector = LightMLObjectDetector()
    boxes, latency_ms = detector.detect_objects(b"test frame person")
    metrics = DetectionMetrics(bounding_boxes=boxes, total_latency_ms=latency_ms)

    assert metrics.total_latency_ms < 40.0 and len(metrics.bounding_boxes) >= 1


def test_light_ml_multi_entity_tracking_boxes() -> None:
    """TR-14: Verify multi-entity concurrent object tracking."""
    detector = LightMLObjectDetector()
    boxes, _ = detector.detect_objects(b"person resident walking near car vehicle and dog pet")

    assert len(boxes) >= 3 and {"person", "vehicle", "dog"}.issubset({b.label for b in boxes})


@pytest.mark.asyncio
async def test_illumination_prefilter_rejects_glare_and_headlights() -> None:
    """TR-15: Verify illumination pre-filter rejects glare and marks event as FALSE_POSITIVE."""
    glare_filter = IlluminationFilter()
    filter_result = glare_filter.evaluate_frame(b"GLARE_SUNLIGHT_OVEREXPOSED_TEST")

    repo = InMemoryEventRepository()
    service = EventProcessingService(repository=repo, illumination_filter=glare_filter)

    event = await service.ingest_raw_event(
        camera_id="cam-backyard",
        payload=b"GLARE_SUNLIGHT_OVEREXPOSED_TEST",
    )

    assert filter_result.is_glare is True and event.category == EventCategory.FALSE_POSITIVE


def test_clip_multiframe_temporal_trajectory_and_best_frame() -> None:
    """Verify Pass 2 multi-frame clip analysis tracks zone trajectory and picks best frame."""
    from lookout.adapters.light_ml import SpatialZoneEvaluator
    from lookout.config.schema import PolygonZoneConfig

    zone_porch = PolygonZoneConfig(
        id="porch",
        name="Front Porch",
        camera_id="cam-front",
        polygon=[(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)],
    )
    zone_lawn = PolygonZoneConfig(
        id="lawn",
        name="Backyard Lawn",
        camera_id="cam-front",
        polygon=[(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)],
    )
    evaluator = SpatialZoneEvaluator(zones=[zone_porch, zone_lawn])
    detector = LightMLObjectDetector(zone_evaluator=evaluator)

    frame1 = b"dog pet on porch standard"
    frame2 = b"dog pet moving to lawn best hq clear keyframe"
    frame3 = b"dog pet walking on lawn"

    result = detector.analyze_clip_frames([frame1, frame2, frame3], camera_id="cam-front")

    assert result.frames_analyzed == 3
    assert result.best_frame_bytes == frame2
    assert result.best_confidence > 0.90
    assert "porch" in result.traversed_zones
    assert "lawn" in result.traversed_zones
    assert result.traversed_zones == ["porch", "lawn"]
    assert "dog" in result.tracked_entities
