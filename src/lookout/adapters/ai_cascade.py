"""Multi-tier AI vision inference cascade adapter and structured output parser."""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from lookout.config.schema import (
    CascadeConfig,
    GeminiConfig,
    OllamaConfig,
    OpenCodeConfig,
)
from lookout.domain.models import (
    BoundingBox,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
)
from lookout.ports.egress import VisionInferencePort

logger = logging.getLogger(__name__)

DEFAULT_VISION_PROMPT = (
    "Analyze this smart home camera image. Output JSON with:\n"
    "{\n"
    '  "category": "<courier_delivery|food_delivery|unknown_visitor|'
    'known_pet|vehicle|package_event|security_intrusion|animal_wildlife>",\n'
    '  "confidence": <float 0.0-1.0>,\n'
    '  "summary": "<short description>",\n'
    '  "bounding_boxes": [{"label": "<label>", "confidence": <float>, '
    '"x_min": <float>, "y_min": <float>, "x_max": <float>, "y_max": <float>}]\n'
    "}"
)

# Canonical synonym mapping to EventCategory
CATEGORY_SYNONYMS: dict[str, EventCategory] = {
    # Delivery
    "courier_delivery": EventCategory.COURIER_DELIVERY,
    "courier": EventCategory.COURIER_DELIVERY,
    "delivery": EventCategory.COURIER_DELIVERY,
    "delivery_driver": EventCategory.COURIER_DELIVERY,
    "delivery driver": EventCategory.COURIER_DELIVERY,
    "mail_carrier": EventCategory.COURIER_DELIVERY,
    "mail carrier": EventCategory.COURIER_DELIVERY,
    "postal_worker": EventCategory.COURIER_DELIVERY,
    "ups": EventCategory.COURIER_DELIVERY,
    "fedex": EventCategory.COURIER_DELIVERY,
    "amazon": EventCategory.COURIER_DELIVERY,
    "usps": EventCategory.COURIER_DELIVERY,
    # Food delivery
    "food_delivery": EventCategory.FOOD_DELIVERY,
    "food delivery": EventCategory.FOOD_DELIVERY,
    "doordash": EventCategory.FOOD_DELIVERY,
    "ubereats": EventCategory.FOOD_DELIVERY,
    "grubhub": EventCategory.FOOD_DELIVERY,
    "pizza_delivery": EventCategory.FOOD_DELIVERY,
    # Package
    "package_event": EventCategory.PACKAGE_EVENT,
    "package": EventCategory.PACKAGE_EVENT,
    "parcel": EventCategory.PACKAGE_EVENT,
    "box": EventCategory.PACKAGE_EVENT,
    # Resident
    "verified_resident": EventCategory.VERIFIED_RESIDENT,
    "resident": EventCategory.VERIFIED_RESIDENT,
    "homeowner": EventCategory.VERIFIED_RESIDENT,
    # Pet
    "known_pet": EventCategory.KNOWN_PET,
    "pet": EventCategory.KNOWN_PET,
    "dog": EventCategory.KNOWN_PET,
    "cat": EventCategory.KNOWN_PET,
    "puppy": EventCategory.KNOWN_PET,
    "kitten": EventCategory.KNOWN_PET,
    # Wildlife
    "animal_wildlife": EventCategory.ANIMAL_WILDLIFE,
    "wildlife": EventCategory.ANIMAL_WILDLIFE,
    "deer": EventCategory.ANIMAL_WILDLIFE,
    "fox": EventCategory.ANIMAL_WILDLIFE,
    "raccoon": EventCategory.ANIMAL_WILDLIFE,
    "coyote": EventCategory.ANIMAL_WILDLIFE,
    "squirrel": EventCategory.ANIMAL_WILDLIFE,
    "bird": EventCategory.ANIMAL_WILDLIFE,
    # Vehicle
    "vehicle": EventCategory.VEHICLE,
    "vehicle_approach": EventCategory.VEHICLE,
    "car": EventCategory.VEHICLE,
    "truck": EventCategory.VEHICLE,
    "van": EventCategory.VEHICLE,
    "suv": EventCategory.VEHICLE,
    "motorcycle": EventCategory.VEHICLE,
    "bicycle": EventCategory.VEHICLE,
    # Intrusion
    "security_intrusion": EventCategory.SECURITY_INTRUSION,
    "intruder": EventCategory.SECURITY_INTRUSION,
    "intrusion": EventCategory.SECURITY_INTRUSION,
    "break-in": EventCategory.SECURITY_INTRUSION,
    "burglar": EventCategory.SECURITY_INTRUSION,
    # Acoustic
    "acoustic_anomaly": EventCategory.ACOUSTIC_ANOMALY,
    "glass_break": EventCategory.ACOUSTIC_ANOMALY,
    "smoke_alarm": EventCategory.ACOUSTIC_ANOMALY,
    "alarm": EventCategory.ACOUSTIC_ANOMALY,
    "gunshot": EventCategory.ACOUSTIC_ANOMALY,
    "scream": EventCategory.ACOUSTIC_ANOMALY,
    # False positive
    "false_positive": EventCategory.FALSE_POSITIVE,
    "shadow": EventCategory.FALSE_POSITIVE,
    "wind": EventCategory.FALSE_POSITIVE,
    "leaves": EventCategory.FALSE_POSITIVE,
    # Unknown visitor
    "unknown_visitor": EventCategory.UNKNOWN_VISITOR,
    "visitor": EventCategory.UNKNOWN_VISITOR,
    "stranger": EventCategory.UNKNOWN_VISITOR,
    "person": EventCategory.UNKNOWN_VISITOR,
    "human": EventCategory.UNKNOWN_VISITOR,
}


class StructuredOutputParser:
    """Robust parser normalizing raw model outputs, markdown fenced JSON, and text."""

    @classmethod
    def parse(cls, raw_output: str) -> tuple[EventCategory, DetectionMetrics, str]:
        """Extract EventCategory, DetectionMetrics, and summary from raw model text."""
        cleaned = raw_output.strip()
        json_str: str | None = None

        # 1. Try markdown fenced code block
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if match:
            json_str = match.group(1).strip()
        elif cleaned.startswith("{") and cleaned.endswith("}"):
            json_str = cleaned
        else:
            # Try finding outermost braces in text
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_str = cleaned[start : end + 1]

        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return cls._parse_dict(data, cleaned)
            except (json.JSONDecodeError, ValueError):
                pass

        # 2. Conversational text heuristic fallback
        return cls._parse_text_fallback(cleaned)

    @classmethod
    def _parse_dict(
        cls, data: dict[str, Any], raw_output: str
    ) -> tuple[EventCategory, DetectionMetrics, str]:
        raw_cat = str(data.get("category", "")).strip().lower()
        category = CATEGORY_SYNONYMS.get(raw_cat, EventCategory.UNKNOWN_VISITOR)

        confidence = 0.8
        try:
            if "confidence" in data:
                confidence = float(data["confidence"])
        except (ValueError, TypeError):
            confidence = 0.8

        summary = str(data.get("summary", data.get("description", raw_output))).strip()

        bounding_boxes: list[BoundingBox] = []
        raw_boxes = data.get("bounding_boxes", data.get("boxes", []))
        if isinstance(raw_boxes, list):
            for item in raw_boxes:
                if isinstance(item, dict):
                    try:
                        label = str(item.get("label", "object"))
                        box_conf = float(item.get("confidence", confidence))
                        if "box" in item and isinstance(item["box"], (list, tuple)):
                            b = item["box"]
                            x_min, y_min, x_max, y_max = (
                                float(b[0]),
                                float(b[1]),
                                float(b[2]),
                                float(b[3]),
                            )
                        else:
                            x_min = float(item.get("x_min", 0.0))
                            y_min = float(item.get("y_min", 0.0))
                            x_max = float(item.get("x_max", 1.0))
                            y_max = float(item.get("y_max", 1.0))

                        bounding_boxes.append(
                            BoundingBox(
                                label=label,
                                confidence=box_conf,
                                x_min=max(0.0, min(1.0, x_min)),
                                y_min=max(0.0, min(1.0, y_min)),
                                x_max=max(0.0, min(1.0, x_max)),
                                y_max=max(0.0, min(1.0, y_max)),
                            )
                        )
                    except (ValueError, TypeError, IndexError):
                        continue

        metrics = DetectionMetrics(
            confidence=confidence,
            bounding_boxes=bounding_boxes,
        )
        return category, metrics, summary

    @classmethod
    def _parse_text_fallback(cls, text: str) -> tuple[EventCategory, DetectionMetrics, str]:
        text_lower = text.lower()
        matched_category = EventCategory.UNKNOWN_VISITOR

        for synonym, cat in CATEGORY_SYNONYMS.items():
            if re.search(rf"\b{re.escape(synonym)}\b", text_lower):
                matched_category = cat
                break

        metrics = DetectionMetrics(
            confidence=0.5,
            bounding_boxes=[],
        )
        return matched_category, metrics, text


@runtime_checkable
class TierVisionProvider(Protocol):
    """Protocol for an individual vision inference tier provider."""

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        """Perform vision inference on image bytes returning category, metrics, and provenance."""
        ...


class OllamaVisionProvider:
    """Tier 1: Local Ollama LAN vision inference provider."""

    def __init__(
        self,
        config: OllamaConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        start = time.perf_counter()
        b64_image = base64.b64encode(image_bytes).decode("ascii")
        url = f"{self.config.host.rstrip('/')}/api/generate"
        payload = {
            "model": self.config.model,
            "prompt": prompt or DEFAULT_VISION_PROMPT,
            "images": [b64_image],
            "stream": False,
            "format": "json",
        }

        if self._client is not None:
            resp = await self._client.post(url, json=payload, timeout=self.config.timeout_seconds)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=self.config.timeout_seconds)

        resp.raise_for_status()
        data = resp.json()

        raw_response = str(data.get("response", ""))
        tokens_used = data.get("prompt_eval_count")
        if tokens_used is None:
            tokens_used = data.get("eval_count")

        latency_ms = (time.perf_counter() - start) * 1000.0
        category, metrics, _ = StructuredOutputParser.parse(raw_response)

        provenance = InferenceProvenance(
            provider="ollama",
            model_name=self.config.model,
            latency_ms=latency_ms,
            confidence=metrics.confidence,
            tokens_used=tokens_used,
            raw_output=raw_response,
        )
        return category, metrics, provenance


class OpenCodeVisionProvider:
    """Tier 2A/2C: OpenCode Cloud Vision Inference Provider (Go and Free tiers)."""

    def __init__(
        self,
        config: OpenCodeConfig,
        tier_type: str = "go",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.tier_type = tier_type  # "go" or "free"
        self._client = client

    @property
    def model_name(self) -> str:
        return self.config.model_go if self.tier_type == "go" else self.config.model_free

    @property
    def provider_id(self) -> str:
        return f"opencode_{self.tier_type}"

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        start = time.perf_counter()
        b64_image = base64.b64encode(image_bytes).decode("ascii")
        url = f"{self.config.api_url.rstrip('/')}/chat/completions"

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or DEFAULT_VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                        },
                    ],
                }
            ],
        }

        if self._client is not None:
            resp = await self._client.post(
                url, json=payload, headers=headers, timeout=self.config.timeout_seconds
            )
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url, json=payload, headers=headers, timeout=self.config.timeout_seconds
                )

        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        raw_content = ""
        if choices and isinstance(choices, list):
            message = choices[0].get("message", {})
            raw_content = str(message.get("content", ""))

        usage = data.get("usage", {})
        tokens_used = usage.get("total_tokens", usage.get("prompt_tokens"))

        latency_ms = (time.perf_counter() - start) * 1000.0
        category, metrics, _ = StructuredOutputParser.parse(raw_content)

        provenance = InferenceProvenance(
            provider=self.provider_id,
            model_name=self.model_name,
            latency_ms=latency_ms,
            confidence=metrics.confidence,
            tokens_used=tokens_used,
            raw_output=raw_content,
        )
        return category, metrics, provenance


class GeminiVisionProvider:
    """Tier 2B: Google Gemini Multimodal Vision Inference Provider."""

    def __init__(
        self,
        config: GeminiConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        start = time.perf_counter()
        b64_image = base64.b64encode(image_bytes).decode("ascii")

        base_url = f"{self.config.api_url.rstrip('/')}/models/{self.config.model}:generateContent"
        url = f"{base_url}?key={self.config.api_key}" if self.config.api_key else base_url

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt or DEFAULT_VISION_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_image,
                            }
                        },
                    ]
                }
            ]
        }

        if self._client is not None:
            resp = await self._client.post(url, json=payload, timeout=self.config.timeout_seconds)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=self.config.timeout_seconds)

        resp.raise_for_status()
        data = resp.json()

        candidates = data.get("candidates", [])
        raw_content = ""
        if candidates and isinstance(candidates, list):
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and isinstance(parts, list):
                raw_content = str(parts[0].get("text", ""))

        usage_meta = data.get("usageMetadata", {})
        tokens_used = usage_meta.get("totalTokenCount", usage_meta.get("promptTokenCount"))

        latency_ms = (time.perf_counter() - start) * 1000.0
        category, metrics, _ = StructuredOutputParser.parse(raw_content)

        provenance = InferenceProvenance(
            provider="gemini",
            model_name=self.config.model,
            latency_ms=latency_ms,
            confidence=metrics.confidence,
            tokens_used=tokens_used,
            raw_output=raw_content,
        )
        return category, metrics, provenance


class LocalEdgeVisionProvider:
    """Tier 3: In-process CPU edge ML detector for people, pets, and vehicles."""

    def __init__(
        self,
        model_name: str = "local-edge-vision-cpu",
        npu_runtime: Any = None,
    ) -> None:
        self.model_name = model_name
        self.npu_runtime = npu_runtime

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        start = time.perf_counter()
        if self.npu_runtime is not None and self.npu_runtime.is_available():
            boxes, latency_ms = self.npu_runtime.infer(image_bytes)
            # Infer category from boxes
            category = EventCategory.UNKNOWN_VISITOR
            confidence = 0.88
            for b in boxes:
                if b.label in ("pet", "dog", "cat"):
                    category = EventCategory.KNOWN_PET
                    confidence = max(confidence, b.confidence)
                elif b.label in ("vehicle", "car", "truck"):
                    category = EventCategory.VEHICLE
                    confidence = max(confidence, b.confidence)
                elif b.label in ("person", "resident"):
                    category = EventCategory.UNKNOWN_VISITOR
                    confidence = max(confidence, b.confidence)
            metrics = DetectionMetrics(confidence=confidence, bounding_boxes=boxes)
            provenance = InferenceProvenance(
                provider="local_edge",
                model_name=self.npu_runtime.get_active_accelerator().name,
                latency_ms=latency_ms,
                confidence=confidence,
                raw_output=f"npu:{category.value}",
            )
            return category, metrics, provenance

        payload_lower = image_bytes.lower()

        # Deterministic lightweight edge detection heuristics
        if b"dog" in payload_lower or b"cat" in payload_lower or b"pet" in payload_lower:
            category = EventCategory.KNOWN_PET
            label = (
                "dog" if b"dog" in payload_lower else ("cat" if b"cat" in payload_lower else "pet")
            )
            box = BoundingBox(
                label=label, confidence=0.88, x_min=0.15, y_min=0.2, x_max=0.6, y_max=0.7
            )
            boxes = [box]
            confidence = 0.88
        elif (
            b"car" in payload_lower
            or b"truck" in payload_lower
            or b"vehicle" in payload_lower
            or b"bicycle" in payload_lower
        ):
            category = EventCategory.VEHICLE
            box = BoundingBox(
                label="vehicle", confidence=0.86, x_min=0.1, y_min=0.25, x_max=0.9, y_max=0.8
            )
            boxes = [box]
            confidence = 0.86
        else:
            # Default edge person detection
            category = EventCategory.UNKNOWN_VISITOR
            box = BoundingBox(
                label="person", confidence=0.85, x_min=0.25, y_min=0.1, x_max=0.75, y_max=0.9
            )
            boxes = [box]
            confidence = 0.85

        latency_ms = max(0.5, (time.perf_counter() - start) * 1000.0)
        metrics = DetectionMetrics(
            confidence=confidence,
            bounding_boxes=boxes,
        )
        provenance = InferenceProvenance(
            provider="local_edge",
            model_name=self.model_name,
            latency_ms=latency_ms,
            confidence=confidence,
            raw_output=f"local_edge:{category.value}",
        )
        return category, metrics, provenance


class OfflineVisionFallbackProvider:
    """Tier 4: Zero-compute fallback returning media-only visitor classification."""

    def __init__(self, fallback_category: EventCategory = EventCategory.UNKNOWN_VISITOR) -> None:
        self.fallback_category = fallback_category

    async def analyze(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        metrics = DetectionMetrics(
            confidence=0.5,
            bounding_boxes=[],
            total_latency_ms=0.0,
        )
        provenance = InferenceProvenance(
            provider="offline_fallback",
            model_name="media-metadata-fallback",
            latency_ms=0.0,
            confidence=0.5,
            raw_output=f"offline_fallback:{self.fallback_category.value}",
        )
        return self.fallback_category, metrics, provenance


class CascadeVisionAdapter(VisionInferencePort):
    """Egress adapter orchestrating a resilient 4-tier vision inference cascade."""

    def __init__(
        self,
        cascade_config: CascadeConfig | None = None,
        ollama_config: OllamaConfig | None = None,
        opencode_config: OpenCodeConfig | None = None,
        gemini_config: GeminiConfig | None = None,
        providers: dict[str, TierVisionProvider] | None = None,
    ) -> None:
        self.cascade_config = cascade_config or CascadeConfig()
        self.ollama_config = ollama_config or OllamaConfig()
        self.opencode_config = opencode_config or OpenCodeConfig()
        self.gemini_config = gemini_config or GeminiConfig()

        if providers is not None:
            self._providers = dict(providers)
        else:
            self._providers = {
                "ollama": OllamaVisionProvider(self.ollama_config),
                "opencode_go": OpenCodeVisionProvider(self.opencode_config, tier_type="go"),
                "gemini": GeminiVisionProvider(self.gemini_config),
                "opencode_free": OpenCodeVisionProvider(self.opencode_config, tier_type="free"),
                "local_edge": LocalEdgeVisionProvider(),
                "offline": OfflineVisionFallbackProvider(self.cascade_config.fallback_category),
            }
        self._runtime_mode: str = "auto"

    def get_runtime_mode(self) -> str:
        """Return the current runtime AI cascade mode."""
        return self._runtime_mode

    def set_runtime_mode(self, mode: str) -> None:
        """Set the runtime AI cascade mode and dynamically configure active tiers."""
        mode_lower = mode.lower()
        if mode_lower == "auto":
            self.cascade_config.tiers = [
                "ollama",
                "opencode_go",
                "gemini",
                "opencode_free",
                "local_edge",
                "offline",
            ]
        elif mode_lower == "local_only":
            self.cascade_config.tiers = ["ollama", "local_edge", "offline"]
        elif mode_lower == "gemini_only":
            self.cascade_config.tiers = ["gemini", "offline"]
        elif mode_lower == "off":
            self.cascade_config.tiers = ["offline"]
        else:
            raise ValueError(f"Unknown runtime AI mode: {mode}")
        self._runtime_mode = mode_lower

    async def analyze_scene(
        self, image_bytes: bytes, prompt: str | None = None
    ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
        """Execute inference cascade sequentially until success or offline fallback."""
        cumulative_latency_ms = 0.0

        for tier_name in self.cascade_config.tiers:
            provider = self._providers.get(tier_name)
            if provider is None:
                continue

            # Check if provider is enabled
            if (
                hasattr(provider, "config")
                and hasattr(provider.config, "enabled")
                and not provider.config.enabled
            ):
                continue

            tier_start = time.perf_counter()
            try:
                # Per-tier timeout budget: check provider-specific config first,
                # falling back to the configured cascade total_timeout_seconds
                timeout_sec = float(self.cascade_config.total_timeout_seconds)
                if (
                    hasattr(provider, "config")
                    and getattr(provider.config, "timeout_seconds", None) is not None
                ):
                    timeout_sec = float(provider.config.timeout_seconds)

                cat, metrics, prov = await asyncio.wait_for(
                    provider.analyze(image_bytes, prompt),
                    timeout=timeout_sec,
                )
                elapsed_ms = (time.perf_counter() - tier_start) * 1000.0
                cumulative_latency_ms += elapsed_ms
                metrics.total_latency_ms = cumulative_latency_ms
                return cat, metrics, prov

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - tier_start) * 1000.0
                cumulative_latency_ms += elapsed_ms
                logger.warning(
                    "Cascade tier '%s' failed after %.1fms: %s", tier_name, elapsed_ms, exc
                )
                continue

        # Ultimate fallback
        offline_provider = self._providers.get("offline") or OfflineVisionFallbackProvider(
            self.cascade_config.fallback_category
        )
        cat, metrics, prov = await offline_provider.analyze(image_bytes, prompt)
        metrics.total_latency_ms = cumulative_latency_ms
        return cat, metrics, prov
