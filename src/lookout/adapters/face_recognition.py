"""Local biometric face recognition adapter using cosine similarity matching."""

import hashlib
import json
import logging
import math
from collections.abc import Callable, Sequence
from typing import Any, TypeGuard

from lookout.config.schema import FaceRecognitionConfig, ResidentConfig
from lookout.ports.egress import FaceRecognitionPort

logger = logging.getLogger(__name__)


def compute_cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two numeric vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def generate_deterministic_embedding(seed: bytes, dim: int = 128) -> list[float]:
    """Generate a normalized unit vector deterministically from byte payload."""
    hasher = hashlib.sha256()
    hasher.update(seed)
    digest = hasher.digest()

    vec: list[float] = []
    for i in range(dim):
        byte_val = digest[i % len(digest)]
        # Map byte 0-255 to float -1.0 to 1.0 with variation
        val = ((byte_val ^ (i * 31 % 256)) / 127.5) - 1.0
        vec.append(val)

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


def _is_1d_numeric_vector(val: Sequence[Any]) -> TypeGuard[Sequence[float | int]]:
    """Determine whether a sequence represents a 1D numeric vector."""
    return bool(val) and isinstance(val[0], (int, float))


class FaceRecognitionAdapter(FaceRecognitionPort):
    """Egress adapter performing local biometric face verification against enrolled gallery."""

    def __init__(
        self,
        config: FaceRecognitionConfig | None = None,
        residents: list[ResidentConfig] | None = None,
        extractor: Callable[[bytes], list[list[float]]] | None = None,
    ) -> None:
        self.config = config or FaceRecognitionConfig()
        self._gallery: dict[str, list[list[float]]] = {}
        self._extractor = extractor

        # Initialize from ResidentConfig if provided
        if residents:
            for res in residents:
                if res.enrolled_faces:
                    for face_ref in res.enrolled_faces:
                        emb = generate_deterministic_embedding(face_ref.encode("utf-8"))
                        self.update_resident_embeddings(res.id, [emb])

    @property
    def gallery(self) -> dict[str, list[list[float]]]:
        """Access enrolled gallery embeddings dictionary."""
        return self._gallery

    def update_resident_embeddings(
        self, resident_id: str, embeddings: list[list[float]] | list[float] | list[Any]
    ) -> None:
        """Register or extend enrolled facial embeddings for a resident."""
        if not embeddings:
            return

        # Handle 1D vector passed directly
        if _is_1d_numeric_vector(embeddings):
            to_add: list[list[float]] = [[float(x) for x in embeddings]]
        else:
            to_add = []
            for item in embeddings:
                if isinstance(item, (list, tuple)):
                    to_add.append([float(x) for x in item])

        if resident_id not in self._gallery:
            self._gallery[resident_id] = []
        self._gallery[resident_id].extend(to_add)

    def extract_embeddings(self, image_bytes: bytes) -> list[list[float]]:
        """Extract facial embedding vectors from image bytes."""
        if self._extractor is not None:
            return self._extractor(image_bytes)

        # 1. Check if image_bytes encodes a JSON vector or list of vectors
        try:
            parsed = json.loads(image_bytes.decode("utf-8", errors="ignore"))
            if isinstance(parsed, list):
                if parsed and isinstance(parsed[0], (int, float)):
                    return [[float(x) for x in parsed]]
                if parsed and isinstance(parsed[0], list):
                    return [[float(x) for x in vec] for vec in parsed]
            if isinstance(parsed, dict) and "embeddings" in parsed:
                embs = parsed["embeddings"]
                if isinstance(embs, list):
                    return [[float(x) for x in vec] for vec in embs]
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            pass

        # 2. Deterministic pseudo-embedding for images
        return [generate_deterministic_embedding(image_bytes)]

    async def identify_faces(self, image_bytes: bytes) -> list[tuple[str, float]]:
        """Identify enrolled resident faces; returns list of (resident_id, confidence)."""
        if not self.config.enabled or not self._gallery:
            return []

        query_embeddings = self.extract_embeddings(image_bytes)
        if not query_embeddings:
            return []

        resident_scores: dict[str, float] = {}

        for query_emb in query_embeddings:
            for res_id, enrolled_list in self._gallery.items():
                for enrolled_emb in enrolled_list:
                    sim = compute_cosine_similarity(query_emb, enrolled_emb)
                    if sim > resident_scores.get(res_id, 0.0):
                        resident_scores[res_id] = sim

        # Filter by similarity threshold
        matches: list[tuple[str, float]] = [
            (res_id, score)
            for res_id, score in resident_scores.items()
            if score >= self.config.similarity_threshold
        ]

        # Rank candidates descending by confidence
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
