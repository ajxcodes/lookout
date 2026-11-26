"""Unit tests for YAMNet acoustic classifier and distress detection."""

from datetime import UTC, datetime

import pytest

from lookout.adapters.audio_classifier import (
    YamnetAudioClassifierAdapter,
    resolve_acoustic_category,
)
from lookout.adapters.memory import InMemoryEventRepository
from lookout.config.schema import AudioClassifierConfig
from lookout.domain.models import (
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import AudioClassifierPort
from tests.fixtures.inference_samples import (
    BARK_SOUND_ALARM_SAMPLE,
    CAR_ALARM_AUDIO,
    DOG_BARK_AUDIO,
    DOORBELL_AUDIO,
    GLASS_BREAK_AUDIO,
    LOW_CONFIDENCE_GLASS_BREAK_AUDIO,
    SILENCE_AUDIO,
    SMOKE_ALARM_AUDIO,
    SPEECH_AUDIO,
)


@pytest.mark.asyncio
async def test_event_processing_service_acoustic_anomaly_escalation() -> None:
    """TR-5: Audio emergency event escalates category to ACOUSTIC_ANOMALY and threat to HIGH."""
    repo = InMemoryEventRepository()
    classifier = YamnetAudioClassifierAdapter()
    service = EventProcessingService(
        repository=repo,
        audio_classifier=classifier,
    )

    payload = {
        "image": b"camera snapshot",
        "audio": b"emergency glass_break sound sample",
    }
    processed = await service.ingest_raw_event("cam-living-room", payload)

    assert processed.category == EventCategory.ACOUSTIC_ANOMALY
    assert processed.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_audio_classifier_emergency_event() -> None:
    """TR-26: Classifies acoustic emergency (glass break) with high confidence."""
    adapter = YamnetAudioClassifierAdapter()
    label, confidence, provenance = await adapter.classify_audio(b"sample audio glass_break noise")

    assert label == "glass_break"
    assert confidence >= 0.85
    assert provenance.provider == "yamnet"


@pytest.mark.asyncio
async def test_audio_classifier_pet_distress() -> None:
    """TR-27: Classifies pet distress (dog barking / whimpering)."""
    adapter = YamnetAudioClassifierAdapter()
    label, confidence, provenance = await adapter.classify_audio(b"audio recording with dog_bark")

    assert label == "dog_bark"
    assert confidence >= 0.85
    assert provenance.provider == "yamnet"


@pytest.mark.asyncio
async def test_audio_classifier_profiling_provenance() -> None:
    """TR-28: Acoustic inference records provenance with provider, model, latency, and output."""
    adapter = YamnetAudioClassifierAdapter(
        config=AudioClassifierConfig(model_path="models/yamnet.tflite")
    )
    label, confidence, provenance = await adapter.classify_audio(b"ambient sound bytes")

    assert provenance.provider == "yamnet"
    assert provenance.model_name == "models/yamnet.tflite"
    assert provenance.latency_ms > 0.0
    assert provenance.confidence == confidence
    assert provenance.raw_output is not None


def test_audio_classifier_implements_audio_classifier_port() -> None:
    """TR-29: YamnetAudioClassifierAdapter implements AudioClassifierPort protocol."""
    adapter = YamnetAudioClassifierAdapter()
    assert isinstance(adapter, AudioClassifierPort)


@pytest.mark.asyncio
async def test_audio_classifier_silence_handling() -> None:
    """Verify empty or silent audio returns silence label."""
    adapter = YamnetAudioClassifierAdapter()
    label, confidence, _ = await adapter.classify_audio(b"")

    assert label == "silence"
    assert confidence >= 0.9


async def test_enrich_event_from_media_yamnet_audio_classification() -> None:
    """TR-12: Verify Pass 2 YAMNet audio enrichment in enrich_event_from_media."""
    from datetime import UTC, datetime

    from lookout.adapters.audio_classifier import YamnetAudioClassifierAdapter
    from lookout.adapters.memory.repository import InMemoryEventRepository
    from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel
    from lookout.domain.service import EventProcessingService

    repo = InMemoryEventRepository()
    classifier = YamnetAudioClassifierAdapter()
    service = EventProcessingService(repository=repo, audio_classifier=classifier)

    event = CameraEvent(
        id="evt-yamnet-1",
        camera_id="living_room",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Motion on living_room",
    )
    await repo.save(event)

    enriched = await service.enrich_event_from_media(
        event.id,
        frame_samples=[],
        audio_bytes=b"sample audio glass_break noise",
    )
    assert enriched is not None
    assert enriched.category == EventCategory.ACOUSTIC_ANOMALY
    assert enriched.threat_level == ThreatLevel.HIGH
    assert any(p.provider == "yamnet" for p in enriched.provenance)


class _StubVision:
    """Vision stub for a confident, catalog-backed pet result on the bark sample."""

    def __init__(self, category: EventCategory, confidence: float = 0.95) -> None:
        self.category = category
        self.confidence = confidence

    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        return (
            self.category,
            DetectionMetrics(confidence=self.confidence),
            InferenceProvenance(
                provider="gemini",
                model_name="gemini-3.5-flash-lite",
                latency_ms=1.0,
                confidence=self.confidence,
            ),
        )


class _StubAudio:
    """Classifier stub returning a fixed label/confidence with YAMNet provenance."""

    def __init__(self, label: str, confidence: float) -> None:
        self.label = label
        self.confidence = confidence

    async def classify_audio(self, audio_bytes: bytes) -> tuple[str, float, InferenceProvenance]:
        return (
            self.label,
            self.confidence,
            InferenceProvenance(
                provider="yamnet",
                model_name="models/yamnet.tflite",
                latency_ms=1.0,
                confidence=self.confidence,
                raw_output=f"{self.label}:{self.confidence:.2f}",
            ),
        )


@pytest.mark.asyncio
async def test_bark_sound_alarm_sample_is_not_acoustic_anomaly() -> None:
    """TR-15 (AC4): the sampled Sound/bark event stays a benign pet event."""
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        vision_inference=_StubVision(EventCategory.KNOWN_PET, 0.95),
        audio_classifier=_StubAudio("dog_bark", 0.89),
    )
    event = await service.ingest_raw_event("cam-porch", BARK_SOUND_ALARM_SAMPLE)
    assert event.category == EventCategory.KNOWN_PET
    assert "Sound alarm" not in event.summary
    assert event.metadata["audio_classification"]["label"] == "dog_bark"
    assert event.metadata["audio_classification"]["benign"] is True


@pytest.mark.asyncio
async def test_low_confidence_audio_abstains_in_both_passes() -> None:
    """TR-16 (AC6): below-threshold audio abstains in ingest and enrichment."""
    repo = InMemoryEventRepository()
    service = EventProcessingService(
        repository=repo,
        audio_classifier=_StubAudio("glass_break", 0.05),
    )
    ingested = await service.ingest_raw_event(
        "cam", {"image": b"camera frame", "audio": LOW_CONFIDENCE_GLASS_BREAK_AUDIO}
    )
    assert ingested.category == EventCategory.ABSTAINED
    assert ingested.threat_level == ThreatLevel.INFO
    assert ingested.metadata["abstain_reason"] == "low_confidence"

    event = CameraEvent(
        id="low-conf-1",
        camera_id="cam",
        timestamp=datetime.now(UTC),
        category=EventCategory.ABSTAINED,
        threat_level=ThreatLevel.INFO,
        summary="Unclassified activity on camera cam",
    )
    await repo.save(event)
    enriched = await service.enrich_event_from_media(
        event.id,
        frame_samples=[],
        audio_bytes=LOW_CONFIDENCE_GLASS_BREAK_AUDIO,
    )
    assert enriched is not None
    assert enriched.category == EventCategory.ABSTAINED
    assert enriched.threat_level == ThreatLevel.INFO
    assert enriched.metadata["abstain_reason"] == "low_confidence"


def test_sound_taxonomy_uses_configurable_label_category_table() -> None:
    """TR-17 (AC5): the default table and a user override are honored."""
    config = AudioClassifierConfig()
    for label in ("car_alarm", "smoke_alarm", "siren", "glass_break", "gunshot", "scream"):
        assert config.sound_label_categories[label] == EventCategory.ACOUSTIC_ANOMALY
    for label in (
        "bark",
        "dog_bark",
        "doorbell",
        "speech",
        "other",
        "ambient",
        "silence",
        "alarm",
    ):
        assert config.sound_label_categories[label] is None

    override = AudioClassifierConfig(
        sound_label_categories={"car_alarm": EventCategory.SECURITY_INTRUSION}
    )
    assert resolve_acoustic_category("car_alarm", 0.9, override) == (
        EventCategory.SECURITY_INTRUSION,
        None,
    )


@pytest.mark.asyncio
async def test_classifier_emits_distinct_taxonomy_labels() -> None:
    """TR-18 (AC5): compound labels win over the generic 'alarm' substring."""
    adapter = YamnetAudioClassifierAdapter()
    for payload, expected in (
        (SMOKE_ALARM_AUDIO, "smoke_alarm"),
        (CAR_ALARM_AUDIO, "car_alarm"),
        (DOORBELL_AUDIO, "doorbell"),
        (SPEECH_AUDIO, "speech"),
    ):
        label, _, _ = await adapter.classify_audio(payload)
        assert label == expected, f"{payload!r} classified as {label}"

    label, confidence, _ = await adapter.classify_audio(SILENCE_AUDIO)
    assert label == "silence" and confidence >= 0.95
    label, confidence, _ = await adapter.classify_audio(GLASS_BREAK_AUDIO)
    assert label == "glass_break" and confidence >= 0.85
    label, confidence, _ = await adapter.classify_audio(DOG_BARK_AUDIO)
    assert label == "dog_bark" and confidence >= 0.85
