"""Mock vision inference adapter for deterministic scene analysis."""

from datetime import UTC, datetime

from lookout.domain.models import (
    BoundingBox,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
)
from lookout.ports.egress import VisionInferencePort


class MockVisionInferenceAdapter(VisionInferencePort):
    """Deterministic vision inference adapter for testing."""

    def __init__(
        self,
        category: EventCategory = EventCategory.UNKNOWN_VISITOR,
        metrics: DetectionMetrics | None = None,
        provenance: InferenceProvenance | None = None,
    ) -> None:
        self.category = category
        self.metrics = metrics or DetectionMetrics(
            confidence=0.88,
            bounding_boxes=[
                BoundingBox(
                    label="person",
                    confidence=0.88,
                    x_min=0.1,
                    y_min=0.1,
                    x_max=0.5,
                    y_max=0.8,
                )
            ],
            duration_seconds=1.0,
            total_latency_ms=12.5,
        )
        self.provenance = provenance or InferenceProvenance(
            provider="mock",
            model_name="mock-vision-v1",
            latency_ms=12.5,
            confidence=0.88,
            timestamp=datetime.now(UTC),
        )

    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        """Return configured deterministic scene analysis."""
        return self.category, self.metrics, self.provenance
