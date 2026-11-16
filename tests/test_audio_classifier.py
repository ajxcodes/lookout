"""Unit tests for YAMNet acoustic classifier and distress detection."""

import pytest

from lookout.adapters.audio_classifier import YamnetAudioClassifierAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.config.schema import AudioClassifierConfig
from lookout.domain.models import EventCategory, ThreatLevel
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import AudioClassifierPort


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
