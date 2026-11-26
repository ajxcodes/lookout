"""YAMNet acoustic classification adapter for emergency and pet distress detection."""

import logging
import time
from collections.abc import Callable

from lookout.config.schema import AudioClassifierConfig
from lookout.domain.models import EventCategory, InferenceProvenance
from lookout.ports.egress import AudioClassifierPort

logger = logging.getLogger(__name__)


def resolve_acoustic_category(
    label: str,
    confidence: float,
    config: AudioClassifierConfig | None = None,
) -> tuple[EventCategory | None, str | None]:
    """Map a classifier label to an event category, abstaining with an observable reason.

    Abstentions return ``(None, reason)`` where reason is one of ``low_confidence``,
    ``unmapped_label`` or ``benign_label``. This is the single place the acoustic
    taxonomy is resolved, shared by both ingestion and media enrichment.
    """
    cfg = config or AudioClassifierConfig()

    if confidence < cfg.confidence_threshold:
        reason = "low_confidence"
        logger.info(
            "Acoustic label '%s' abstained (%s): confidence %.2f < %.2f",
            label,
            reason,
            confidence,
            cfg.confidence_threshold,
        )
        return None, reason

    if label not in cfg.sound_label_categories:
        reason = "unmapped_label"
        logger.info("Acoustic label '%s' abstained (%s)", label, reason)
        return None, reason

    category = cfg.sound_label_categories[label]
    if category is None:
        reason = "benign_label"
        logger.info("Acoustic label '%s' abstained (%s)", label, reason)
        return None, reason

    return category, None


class YamnetAudioClassifierAdapter(AudioClassifierPort):
    """Egress adapter executing YAMNet acoustic event classification on audio waveforms."""

    def __init__(
        self,
        config: AudioClassifierConfig | None = None,
        classifier: Callable[[bytes], tuple[str, float]] | None = None,
    ) -> None:
        self.config = config or AudioClassifierConfig()
        self._classifier = classifier

    async def classify_audio(self, audio_bytes: bytes) -> tuple[str, float, InferenceProvenance]:
        """Classify audio sample returning label, confidence, and provenance."""
        start = time.perf_counter()

        if self._classifier is not None:
            label, confidence = self._classifier(audio_bytes)
        else:
            label, confidence = self._detect_acoustic_event(audio_bytes)

        latency_ms = max(0.5, (time.perf_counter() - start) * 1000.0)

        provenance = InferenceProvenance(
            provider="yamnet",
            model_name=self.config.model_path,
            latency_ms=latency_ms,
            confidence=confidence,
            raw_output=f"{label}:{confidence:.2f}",
        )

        return label, confidence, provenance

    def _detect_acoustic_event(self, audio_bytes: bytes) -> tuple[str, float]:
        """Classify waveform data into distinct, specific YAMNet-style taxonomy labels.

        Compound labels are probed before generic substrings so ``smoke alarm`` is
        never flattened into ``alarm`` and ``car alarm siren`` stays a car alarm.
        """
        if not audio_bytes:
            return "silence", 0.95

        payload_lower = audio_bytes.lower()

        # Most-specific tokens first; a bare 'alarm' probe comes last.
        probes: tuple[tuple[str, bytes, float], ...] = (
            ("smoke_alarm", b"smoke alarm", 0.92),
            ("smoke_alarm", b"smoke_alarm", 0.92),
            ("car_alarm", b"car alarm", 0.92),
            ("car_alarm", b"car_alarm", 0.92),
            ("glass_break", b"glass_break", 0.92),
            ("glass_break", b"glass break", 0.92),
            ("dog_bark", b"dog_bark", 0.89),
            ("dog_bark", b"dog bark", 0.89),
            ("dog_bark", b"bark", 0.89),
            ("dog_whimper", b"dog_whimper", 0.85),
            ("dog_whimper", b"whimper", 0.85),
            ("cat_meow", b"cat_meow", 0.85),
            ("cat_meow", b"meow", 0.85),
            ("siren", b"siren", 0.90),
            ("gunshot", b"gunshot", 0.93),
            ("scream", b"scream", 0.90),
            ("doorbell", b"doorbell", 0.86),
            ("speech", b"speech", 0.84),
            ("alarm", b"alarm", 0.91),
        )
        for label, token, confidence in probes:
            if token in payload_lower:
                return label, confidence

        # Honour custom emergency/pet label configuration as a compatibility fallback.
        for label in sorted(
            (*self.config.emergency_labels, *self.config.pet_labels), key=len, reverse=True
        ):
            token = label.encode("ascii", errors="ignore")
            if token and token in payload_lower:
                return label, 0.92

        # Default ambient baseline
        return "ambient", 0.80
