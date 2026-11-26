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
from lookout.adapters.entity_matcher import EntityCatalogMatcher
from lookout.adapters.face_recognition import FaceRecognitionAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.memory.notification import MockNotificationAdapter
from lookout.adapters.ring_native import _category_from
from lookout.config.schema import (
    CascadeConfig,
    GeminiConfig,
    OllamaConfig,
    OpenCodeConfig,
    PetConfig,
    ResidentConfig,
)
from lookout.domain.models import (
    BoundingBox,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import VisionInferencePort
from tests.fixtures.inference_samples import (
    CATALOG_PET_SAMPLE,
    CATALOG_RESIDENT_SAMPLE,
    FALSE_POSITIVE_SAMPLE,
    UNMAPPED_LABEL_OUTPUT,
    UNPARSEABLE_TEXT_OUTPUT,
)


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
    """No-signal cascade no longer escalates to a guessed unknown visitor."""
    repo = InMemoryEventRepository()
    cascade = CascadeVisionAdapter(cascade_config=CascadeConfig(tiers=["local_edge", "offline"]))
    service = EventProcessingService(
        repository=repo,
        vision_inference=cascade,
    )

    processed = await service.ingest_raw_event("cam-driveway", b"camera image bytes")
    assert processed.threat_level == ThreatLevel.INFO
    assert processed.category == EventCategory.ABSTAINED


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
    assert category == EventCategory.ABSTAINED


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
    assert category == EventCategory.ABSTAINED
    assert provenance.abstain_reason == "all_tiers_unavailable"
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


def test_unparseable_text_fallback_abstains() -> None:
    """TR-21: Unparseable text abstains with an observable reason (AC2)."""
    category, metrics, summary, reason = StructuredOutputParser.parse_detailed(
        UNPARSEABLE_TEXT_OUTPUT
    )
    assert category == EventCategory.ABSTAINED
    assert metrics.confidence == 0.5
    assert len(metrics.bounding_boxes) == 0
    assert reason == "unparseable_output"
    assert summary == UNPARSEABLE_TEXT_OUTPUT


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
    assert category == EventCategory.ABSTAINED


@pytest.mark.asyncio
@respx.mock
async def test_ollama_downscales_large_image() -> None:
    import base64
    import io
    import json

    from PIL import Image

    img = Image.new("RGB", (2000, 2000), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    original = buf.getvalue()

    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        captured["decoded"] = base64.b64decode(body["images"][0])
        return Response(
            200,
            json={
                "response": '{"category": "false_positive", "confidence": 0.5}',
                "prompt_eval_count": 1,
            },
        )

    respx.post("http://localhost:11434/api/generate").mock(side_effect=handler)

    provider = OllamaVisionProvider(OllamaConfig())
    await provider.analyze(original)

    assert len(captured["decoded"]) < len(original)


class _BoxDetector:
    """Minimal light-ML stub returning a fixed set of bounding boxes."""

    def __init__(self, boxes: list[BoundingBox]) -> None:
        self.boxes = boxes

    def detect_objects(self, image_bytes: bytes) -> tuple[list[BoundingBox], float]:
        return self.boxes, 12.0


class _StubVision:
    """Minimal vision stub returning a fixed category with provenance."""

    def __init__(self, category: EventCategory, confidence: float = 0.9) -> None:
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


class _UnmappedLabelVision:
    """Tier carrying the discovery's unmapped-label abstention reason."""

    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        category, metrics, _, reason = StructuredOutputParser.parse_detailed(UNMAPPED_LABEL_OUTPUT)
        return (
            category,
            metrics,
            InferenceProvenance(
                provider="ollama",
                model_name="moondream",
                latency_ms=1.0,
                confidence=metrics.confidence,
                abstain_reason=reason,
            ),
        )


class _FailingVision:
    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        raise RuntimeError("tier unavailable")


@pytest.mark.asyncio
async def test_unmapped_label_abstains_with_observable_reason() -> None:
    """TR-11 (AC2): unmapped VLM labels abstain and expose the reason."""
    category, _, _, reason = StructuredOutputParser.parse_detailed(UNMAPPED_LABEL_OUTPUT)
    assert category == EventCategory.ABSTAINED
    assert reason == "unmapped_label"

    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        vision_inference=_UnmappedLabelVision(),
    )
    event = await service.ingest_raw_event(
        "cam-abstain", {"image": b"snapshot with unmapped label"}
    )
    assert event.category == EventCategory.ABSTAINED
    assert event.threat_level == ThreatLevel.INFO
    assert event.metadata["abstain_reason"] == "unmapped_label"
    assert event.provenance[0].abstain_reason == "unmapped_label"
    assert event.category != EventCategory.UNKNOWN_VISITOR


@pytest.mark.asyncio
async def test_catalog_matched_resident_drives_verified_resident() -> None:
    """TR-12 (AC1): an enrolled catalog match resolves to VERIFIED_RESIDENT."""
    matcher = EntityCatalogMatcher(residents=[ResidentConfig(id="resident-alvin", name="Alvin")])
    detector = _BoxDetector(
        [BoundingBox(label="person", confidence=0.93, x_min=0.1, y_min=0.1, x_max=0.6, y_max=0.8)]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        light_ml_detector=detector,
        entity_matcher=matcher,
    )
    event = await service.ingest_raw_event("cam-porch", CATALOG_RESIDENT_SAMPLE)
    assert event.matched_entities == ["Alvin"]
    assert event.resident_id == "resident-alvin"
    assert event.category == EventCategory.VERIFIED_RESIDENT


@pytest.mark.asyncio
async def test_catalog_matched_pet_survives_vlm_and_audio_overrides() -> None:
    """TR-13 (AC1, AC4): a catalog-matched pet cannot be downgraded by VLM/sound."""
    matcher = EntityCatalogMatcher(pets=[PetConfig(id="pet-mochi", name="Mochi", species="dog")])
    detector = _BoxDetector(
        [BoundingBox(label="dog", confidence=0.90, x_min=0.2, y_min=0.3, x_max=0.7, y_max=0.8)]
    )
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        light_ml_detector=detector,
        entity_matcher=matcher,
        vision_inference=_StubVision(EventCategory.FALSE_POSITIVE, 0.95),
    )
    payload = dict(CATALOG_PET_SAMPLE)
    payload["metadata"] = {"alarm_type": "Sound", "ai_tags": ["Dog Barking"]}
    event = await service.ingest_raw_event("cam-porch", payload)
    assert event.pet_id == "pet-mochi"
    assert event.matched_entities == ["Mochi"]
    assert event.category == EventCategory.KNOWN_PET
    assert "Sound alarm" not in event.summary


@pytest.mark.asyncio
async def test_non_person_motion_false_positives_are_suppressed() -> None:
    """TR-14 (AC3): non-person motion is suppressed/reclassified, not a visitor."""
    service = EventProcessingService(repository=InMemoryEventRepository())
    shadow = await service.ingest_raw_event("cam", FALSE_POSITIVE_SAMPLE)
    assert shadow.category == EventCategory.FALSE_POSITIVE

    for label in ("wind", "leaves"):
        category, _, _, _ = StructuredOutputParser.parse_detailed(
            f'{{"category": "{label}", "confidence": 0.9}}'
        )
        assert category == EventCategory.FALSE_POSITIVE

    wind_service = EventProcessingService(
        repository=InMemoryEventRepository(),
        vision_inference=_StubVision(EventCategory.FALSE_POSITIVE, 0.91),
    )
    wind = await wind_service.ingest_raw_event("cam", {"image": b"wind moving leaves"})
    assert wind.category == EventCategory.FALSE_POSITIVE

    assert _category_from("motion", "") == EventCategory.ABSTAINED
    assert _category_from("ding", "") == EventCategory.ABSTAINED

    person = await service.ingest_raw_event("cam", {"metadata": {"ai_tags": ["person"]}})
    assert person.category == EventCategory.UNKNOWN_VISITOR


@pytest.mark.asyncio
async def test_all_tier_failure_abstains_with_reason() -> None:
    """TR-19 (AC2): all-tier failure abstains with a reason and sends no alert."""
    tiers = ["ollama", "opencode_go", "gemini", "opencode_free", "local_edge", "offline"]
    providers = {name: _FailingVision() for name in tiers if name != "offline"}
    providers["offline"] = OfflineVisionFallbackProvider()
    adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(tiers=tiers),
        providers=providers,  # type: ignore[arg-type]
    )
    category, _, provenance = await adapter.analyze_scene(b"image bytes")
    assert category == EventCategory.ABSTAINED
    assert provenance.abstain_reason == "all_tiers_unavailable"

    notifier = MockNotificationAdapter()
    service = EventProcessingService(
        repository=InMemoryEventRepository(),
        vision_inference=adapter,
        notification=notifier,
    )
    event = await service.ingest_raw_event("cam-failed", {"image": b"image bytes"})
    assert event.category == EventCategory.ABSTAINED
    assert event.threat_level == ThreatLevel.INFO
    assert event.metadata["abstain_reason"] == "all_tiers_unavailable"
    assert len(notifier.sent_notifications) == 0


@pytest.mark.asyncio
async def test_no_signal_vision_tiers_do_not_report_unknown_visitor() -> None:
    """TR-22 (AC2, AC3): no-signal tiers abstain instead of guessing a visitor."""
    import asyncio

    class SlowProvider:
        async def analyze_scene(
            self, image_bytes: bytes, prompt: str | None = None
        ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
            await asyncio.sleep(0.5)
            return (
                EventCategory.PACKAGE_EVENT,
                DetectionMetrics(confidence=0.9),
                InferenceProvenance(
                    provider="slow",
                    model_name="mock",
                    latency_ms=500.0,
                    confidence=0.9,
                ),
            )

    category, metrics, _ = await LocalEdgeVisionProvider().analyze(b"camera image bytes")
    assert category == EventCategory.ABSTAINED
    assert metrics.bounding_boxes == []

    offline_adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(tiers=["offline"]),
        providers={"offline": OfflineVisionFallbackProvider()},
    )
    category, _, _ = await offline_adapter.analyze_scene(b"camera image bytes")
    assert category == EventCategory.ABSTAINED

    timeout_adapter = CascadeVisionAdapter(
        cascade_config=CascadeConfig(tiers=["slow", "offline"], total_timeout_seconds=0.01),
        providers={"slow": SlowProvider(), "offline": OfflineVisionFallbackProvider()},  # type: ignore[dict-item]
    )
    category, _, _ = await timeout_adapter.analyze_scene(b"camera image bytes")
    assert category == EventCategory.ABSTAINED
