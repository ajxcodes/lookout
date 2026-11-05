"""Mock audio classifier adapter for deterministic acoustic classification."""

from datetime import UTC, datetime

from lookout.domain.models import InferenceProvenance
from lookout.ports.egress import AudioClassifierPort


class MockAudioClassifierAdapter(AudioClassifierPort):
    """Deterministic audio classifier adapter for testing."""

    def __init__(
        self,
        label: str = "speech",
        confidence: float = 0.85,
        provenance: InferenceProvenance | None = None,
    ) -> None:
        self.label = label
        self.confidence = confidence
        self.provenance = provenance or InferenceProvenance(
            provider="mock",
            model_name="mock-audio-v1",
            latency_ms=8.0,
            confidence=confidence,
            timestamp=datetime.now(UTC),
        )

    async def classify_audio(self, audio_bytes: bytes) -> tuple[str, float, InferenceProvenance]:
        """Return configured deterministic audio classification."""
        return self.label, self.confidence, self.provenance
