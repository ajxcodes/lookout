"""Unit tests for biometric face recognition and gallery matching."""

import json

import pytest

from lookout.adapters.face_recognition import (
    FaceRecognitionAdapter,
    compute_cosine_similarity,
)
from lookout.config.schema import FaceRecognitionConfig, ResidentConfig
from lookout.ports.egress import FaceRecognitionPort


@pytest.mark.asyncio
async def test_face_recognition_resident_match() -> None:
    """TR-19: Identify enrolled resident face above similarity threshold."""
    adapter = FaceRecognitionAdapter(config=FaceRecognitionConfig(similarity_threshold=0.6))
    alice_vec = [1.0] + [0.0] * 127
    adapter.update_resident_embeddings("alice", [alice_vec])

    # Query with exact or high-similarity vector
    query_bytes = json.dumps(alice_vec).encode("utf-8")
    matches = await adapter.identify_faces(query_bytes)

    assert len(matches) >= 1
    assert matches[0][0] == "alice"
    assert matches[0][1] >= 0.99


@pytest.mark.asyncio
async def test_face_recognition_unknown_stranger_rejected() -> None:
    """TR-20: Stranger below similarity threshold is rejected (returns empty matches)."""
    adapter = FaceRecognitionAdapter(config=FaceRecognitionConfig(similarity_threshold=0.6))
    alice_vec = [1.0] + [0.0] * 127
    adapter.update_resident_embeddings("alice", [alice_vec])

    # Query with orthogonal / dissimilar vector
    stranger_vec = [0.0, 1.0] + [0.0] * 126
    query_bytes = json.dumps(stranger_vec).encode("utf-8")
    matches = await adapter.identify_faces(query_bytes)

    assert len(matches) == 0


@pytest.mark.asyncio
async def test_face_recognition_ranking_multiple_faces() -> None:
    """TR-21: Multiple face matches are ranked in descending order of similarity score."""
    adapter = FaceRecognitionAdapter(config=FaceRecognitionConfig(similarity_threshold=0.5))

    # Base vector
    v_base = [1.0, 0.0, 0.0] + [0.0] * 125
    # Alice: very close vector (cosine sim ~0.95)
    v_alice = [0.95, 0.31, 0.0] + [0.0] * 125
    # Bob: moderately close vector (cosine sim ~0.70)
    v_bob = [0.70, 0.71, 0.0] + [0.0] * 125

    adapter.update_resident_embeddings("alice", [v_alice])
    adapter.update_resident_embeddings("bob", [v_bob])

    query_bytes = json.dumps(v_base).encode("utf-8")
    matches = await adapter.identify_faces(query_bytes)

    assert len(matches) == 2
    assert matches[0][0] == "alice"
    assert matches[1][0] == "bob"
    assert matches[0][1] >= matches[1][1]


def test_face_recognition_implements_face_recognition_port() -> None:
    """TR-22: FaceRecognitionAdapter implements FaceRecognitionPort protocol."""
    adapter = FaceRecognitionAdapter()
    assert isinstance(adapter, FaceRecognitionPort)


@pytest.mark.asyncio
async def test_face_recognition_initialization_from_residents() -> None:
    """Verify enrolled faces from ResidentConfig are registered on initialization."""
    residents = [
        ResidentConfig(
            id="alice",
            name="Alice",
            enrolled_faces=["/faces/alice1.jpg", "/faces/alice2.jpg"],
        )
    ]
    adapter = FaceRecognitionAdapter(residents=residents)
    assert "alice" in adapter.gallery
    assert len(adapter.gallery["alice"]) == 2


def test_cosine_similarity_edge_cases() -> None:
    """Verify cosine similarity handles zero vectors and length mismatches gracefully."""
    assert compute_cosine_similarity([], []) == 0.0
    assert compute_cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert compute_cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
