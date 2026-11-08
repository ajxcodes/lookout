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
