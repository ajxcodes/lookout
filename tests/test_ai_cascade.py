"""Unit tests for AI multi-tier inference cascade, failover, and structured output parsing."""

import httpx
import pytest
import respx
from httpx import Response

from lookout.adapters.ai_cascade import (
    CascadeVisionAdapter,
    GeminiVisionProvider,
    LocalEdgeVisionProvider,
    OfflineVisionFallbackProvider,
    OllamaVisionProvider,
    OpenCodeVisionProvider,
    StructuredOutputParser,
)
from lookout.adapters.face_recognition import FaceRecognitionAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.config.schema import (
    CascadeConfig,
    GeminiConfig,
    OllamaConfig,
    OpenCodeConfig,
)
from lookout.domain.models import EventCategory, ThreatLevel
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import VisionInferencePort


@pytest.mark.asyncio
async def test_event_processing_service_pipeline_with_cascade() -> None:
    """TR-3: End-to-end event pipeline with cascade vision and verified face recognition."""
    repo = InMemoryEventRepository()
    face_adapter = FaceRecognitionAdapter()
    dummy_vec = [1.0] + [0.0] * 127
    face_adapter.update_resident_embeddings("alice", [dummy_vec])

    # Custom mock extractor that returns dummy_vec for this test
    face_adapter._extractor = lambda _b: [dummy_vec]

    cascade = CascadeVisionAdapter(cascade_config=CascadeConfig(tiers=["local_edge", "offline"]))
    service = EventProcessingService(
        repository=repo,
        vision_inference=cascade,
        face_recognition=face_adapter,
    )

    processed = await service.ingest_raw_event("cam-porch", b"camera frame")
    assert processed.category == EventCategory.VERIFIED_RESIDENT
    assert processed.resident_id == "alice"


@pytest.mark.asyncio
async def test_event_processing_service_threat_dispatch_escalation() -> None:
    """TR-4: Threat level escalates to MEDIUM for unknown visitor detection."""
    repo = InMemoryEventRepository()
    cascade = CascadeVisionAdapter(cascade_config=CascadeConfig(tiers=["local_edge", "offline"]))
    service = EventProcessingService(
        repository=repo,
        vision_inference=cascade,
    )

    processed = await service.ingest_raw_event("cam-driveway", b"camera image bytes")
    assert processed.threat_level == ThreatLevel.MEDIUM
    assert processed.category == EventCategory.UNKNOWN_VISITOR


@pytest.mark.asyncio
@respx.mock
async def test_ollama_tier1_inference_success() -> None:
    """TR-6: Tier 1 Ollama LAN execution returns valid category and provenance."""
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=Response(
            200,
            json={
                "response": (
                    '{"category": "courier_delivery", "confidence": 0.94, '
                    '"summary": "Courier dropoff"}'
                ),
                "prompt_eval_count": 42,
            },
        )
    )

    provider = OllamaVisionProvider(OllamaConfig())
    category, metrics, provenance = await provider.analyze(b"test image")

    assert category == EventCategory.COURIER_DELIVERY
    assert provenance.provider == "ollama"
    assert provenance.model_name == "moondream"
    assert metrics.confidence == 0.94


@pytest.mark.asyncio
@respx.mock
async def test_ollama_records_profiling_metadata() -> None:
    """TR-7: Verify token usage and profiling metadata are recorded from Ollama."""
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=Response(
            200,
            json={
                "response": '{"category": "vehicle", "confidence": 0.89}',
                "prompt_eval_count": 42,
            },
        )
    )

    provider = OllamaVisionProvider(OllamaConfig())
    _, _, provenance = await provider.analyze(b"test image")

    assert provenance.tokens_used == 42
    assert provenance.latency_ms > 0.0


@pytest.mark.asyncio
@respx.mock
async def test_cascade_failover_on_ollama_timeout() -> None:
    """TR-8: Cascade fails over to Tier 2A OpenCode Go when Tier 1 Ollama times out."""
    respx.post("http://localhost:11434/api/generate").mock(
        side_effect=httpx.ConnectTimeout("LAN Ollama node connection timed out")
    )
    respx.post("https://api.opencode.ai/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"category": "courier_delivery", "confidence": 0.91}'}}
                ],
                "usage": {"total_tokens": 58},
            },
        )
    )

    adapter = CascadeVisionAdapter()
    category, metrics, provenance = await adapter.analyze_scene(b"image bytes")

    assert category == EventCategory.COURIER_DELIVERY
    assert provenance.provider == "opencode_go"
    assert provenance.model_name == "deepseek-v4.1-flash"


@pytest.mark.asyncio
@respx.mock
async def test_cascade_failover_on_opencode_rate_limit() -> None:
    """TR-9: Cascade fails over to Tier 2B Gemini on OpenCode Go rate limit (HTTP 429)."""
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=Response(503, text="Service Unavailable")
    )
    respx.post("https://api.opencode.ai/v1/chat/completions").mock(
        return_value=Response(429, text="Rate limit exceeded")
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    ).mock(
        return_value=Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"category": "animal_wildlife", '
                                        '"confidence": 0.87, "summary": "Raccoon"}'
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 64},
            },
        )
    )

    adapter = CascadeVisionAdapter()
    category, metrics, provenance = await adapter.analyze_scene(b"image bytes")

    assert category == EventCategory.ANIMAL_WILDLIFE
    assert provenance.provider == "gemini"
    assert provenance.model_name == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
@respx.mock
async def test_cascade_failover_to_opencode_free() -> None:
    """TR-10: Cascade fails over to OpenCode Free when Ollama, OpenCode Go, and Gemini fail."""
    respx.post("http://localhost:11434/api/generate").mock(return_value=Response(500))
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    ).mock(return_value=Response(500))

    # First call (Go tier) fails with 429, second call (Free tier) succeeds with 200
    respx.post("https://api.opencode.ai/v1/chat/completions").mock(
        side_effect=[
            Response(429, text="Rate limit"),
            Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"category": "vehicle", "confidence": 0.85}'}}
                    ],
                    "usage": {"total_tokens": 50},
                },
            ),
        ]
    )

    adapter = CascadeVisionAdapter()
    category, metrics, provenance = await adapter.analyze_scene(b"image bytes")

    assert category == EventCategory.VEHICLE
    assert provenance.provider == "opencode_free"
    assert provenance.model_name == "deepseek-v4.1-flash-free"


@pytest.mark.asyncio
async def test_cascade_failover_to_local_edge_tier3() -> None:
    """TR-11: Cascade falls back to Tier 3 LocalEdgeVisionProvider when all cloud tiers fail."""
    failing_ollama = OllamaVisionProvider(OllamaConfig(enabled=False))
    failing_opencode = OpenCodeVisionProvider(OpenCodeConfig(enabled=False))
    failing_gemini = GeminiVisionProvider(GeminiConfig(enabled=False))

    adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(
            tiers=["ollama", "opencode_go", "gemini", "opencode_free", "local_edge", "offline"]
        ),
        providers={
            "ollama": failing_ollama,
            "opencode_go": failing_opencode,
            "gemini": failing_gemini,
            "opencode_free": failing_opencode,
            "local_edge": LocalEdgeVisionProvider(),
            "offline": OfflineVisionFallbackProvider(),
        },
    )

    category, metrics, provenance = await adapter.analyze_scene(b"image bytes")
    assert provenance.provider == "local_edge"
    assert category == EventCategory.UNKNOWN_VISITOR


@pytest.mark.asyncio
async def test_local_edge_vision_provider_person_detection() -> None:
    """TR-12: LocalEdgeVisionProvider detects person and maps to UNKNOWN_VISITOR."""
    provider = LocalEdgeVisionProvider()
    category, metrics, provenance = await provider.analyze(b"frame data with human in view")

    assert category == EventCategory.UNKNOWN_VISITOR
    assert provenance.provider == "local_edge"
    assert len(metrics.bounding_boxes) == 1
    assert metrics.bounding_boxes[0].label == "person"


@pytest.mark.asyncio
async def test_local_edge_vision_provider_pet_and_vehicle_detection() -> None:
    """TR-13: LocalEdgeVisionProvider detects pet and vehicle from input payload."""
    provider = LocalEdgeVisionProvider()

    pet_cat, pet_metrics, _ = await provider.analyze(b"frame with dog playing outside")
    assert pet_cat == EventCategory.KNOWN_PET
    assert pet_metrics.bounding_boxes[0].label == "dog"

    veh_cat, veh_metrics, _ = await provider.analyze(b"frame with car in driveway")
    assert veh_cat == EventCategory.VEHICLE
    assert veh_metrics.bounding_boxes[0].label == "vehicle"


@pytest.mark.asyncio
async def test_cascade_full_failover_to_offline_fallback_tier4() -> None:
    """TR-14: Full cascade failover reaches Tier 4 OfflineVisionFallbackProvider."""
    adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(tiers=["offline"]),
        providers={"offline": OfflineVisionFallbackProvider()},
    )

    category, metrics, provenance = await adapter.analyze_scene(b"offline image")
    assert provenance.provider == "offline_fallback"
    assert category == EventCategory.UNKNOWN_VISITOR
    assert metrics.total_latency_ms >= 0.0


def test_structured_output_parser_json_fenced() -> None:
    """TR-15: StructuredOutputParser parses markdown code block and maps category."""
    raw = (
        "Here is the detection:\n"
        "```json\n"
        "{\n"
        '  "category": "courier_delivery",\n'
        '  "confidence": 0.96,\n'
        '  "summary": "Courier dropoff on porch"\n'
        "}\n"
        "```"
    )
    category, metrics, summary = StructuredOutputParser.parse(raw)
    assert category == EventCategory.COURIER_DELIVERY
    assert metrics.confidence == 0.96
    assert summary == "Courier dropoff on porch"


def test_structured_output_parser_bounding_boxes() -> None:
    """TR-16: StructuredOutputParser correctly extracts normalized bounding box coordinates."""
    raw = (
        "{\n"
        '  "category": "vehicle",\n'
        '  "confidence": 0.91,\n'
        '  "bounding_boxes": [\n'
        '    {"label": "car", "confidence": 0.92, '
        '"x_min": 0.1, "y_min": 0.2, "x_max": 0.8, "y_max": 0.7}\n'
        "  ]\n"
        "}"
    )
    category, metrics, _ = StructuredOutputParser.parse(raw)
    assert category == EventCategory.VEHICLE
    assert len(metrics.bounding_boxes) == 1
    box = metrics.bounding_boxes[0]
    assert box.label == "car"
    assert box.confidence == 0.92
    assert box.x_min == 0.1
    assert box.x_max == 0.8


def test_structured_output_parser_malformed_fallback() -> None:
    """TR-17: StructuredOutputParser falls back gracefully on malformed output."""
    raw = "Random unparseable garbage output with no structure or recognizable tokens."
    category, metrics, summary = StructuredOutputParser.parse(raw)
    assert category == EventCategory.UNKNOWN_VISITOR
    assert metrics.confidence == 0.5
    assert len(metrics.bounding_boxes) == 0


def test_cascade_implements_vision_inference_port() -> None:
    """TR-18: CascadeVisionAdapter implements VisionInferencePort Protocol."""
    adapter = CascadeVisionAdapter()
    assert isinstance(adapter, VisionInferencePort)


@pytest.mark.asyncio
async def test_cascade_falls_back_to_total_timeout_seconds() -> None:
    """Verify tier without explicit timeout_seconds uses total_timeout_seconds fallback."""
    import asyncio

    from lookout.domain.models import DetectionMetrics, InferenceProvenance

    class SlowMockProvider:
        async def analyze(
            self, image_bytes: bytes, prompt: str | None = None
        ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
            await asyncio.sleep(0.5)
            return (
                EventCategory.PACKAGE_DELIVERY,
                DetectionMetrics(confidence=0.9),
                InferenceProvenance(
                    provider="slow",
                    model_name="mock",
                    latency_ms=500.0,
                    confidence=0.9,
                    raw_output="slow",
                ),
            )

    adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(tiers=["slow", "offline"], total_timeout_seconds=0.05),
        providers={"slow": SlowMockProvider(), "offline": OfflineVisionFallbackProvider()},  # type: ignore[dict-item]
    )

    category, metrics, provenance = await adapter.analyze_scene(b"test")
    # Must have timed out on the slow provider and fallen back to offline_fallback
    assert provenance.provider == "offline_fallback"
    assert category == EventCategory.UNKNOWN_VISITOR
