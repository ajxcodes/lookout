"""Unit and integration tests for Lookout REST API web endpoints."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.ai_cascade import CascadeVisionAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig
from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)


@pytest.fixture
def test_config(tmp_path: Path) -> LookoutConfig:
    """Provide a validated LookoutConfig with temporary storage."""
    return LookoutConfig(
        system_name="Test Lookout",
        storage=StorageConfig(base_path=str(tmp_path / "data")),
        cameras=[
            CameraConfig(
                id="front-porch",
                name="Front Porch",
                stream_url="rtsp://localhost:8554/front_porch",
            ),
            CameraConfig(
                id="backyard",
                name="Backyard",
                stream_url="rtsp://localhost:8554/backyard",
            ),
        ],
    )


@pytest.fixture
def seeded_repo() -> InMemoryEventRepository:
    """Provide an in-memory repository seeded with test domain events."""
    repo = InMemoryEventRepository()
    now = datetime.now(UTC)

    event1 = CameraEvent(
        id="event-1",
        camera_id="front-porch",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier delivery at front porch",
        metrics=DetectionMetrics(
            confidence=0.92,
            bounding_boxes=[BoundingBox("courier", 0.92, 0.2, 0.2, 0.8, 0.8)],
            audio_decibels=-12.5,
            total_latency_ms=180.0,
        ),
        provenance=[
            InferenceProvenance(
                provider="ollama",
                model_name="moondream",
                latency_ms=150.0,
                confidence=0.92,
            )
        ],
    )

    event2 = CameraEvent(
        id="event-2",
        camera_id="backyard",
        timestamp=now,
        category=EventCategory.KNOWN_PET,
        threat_level=ThreatLevel.INFO,
        summary="Mochi roaming in backyard",
        metrics=DetectionMetrics(
            confidence=0.88,
            bounding_boxes=[BoundingBox("dog", 0.88, 0.1, 0.3, 0.5, 0.7)],
            audio_decibels=-24.0,
            total_latency_ms=90.0,
        ),
        provenance=[
            InferenceProvenance(
                provider="local_edge",
                model_name="local-edge-vision-cpu",
                latency_ms=85.0,
                confidence=0.88,
            )
        ],
    )

    repo._events["event-1"] = event1
    repo._events["event-2"] = event2
    return repo


@pytest.fixture
def client(test_config: LookoutConfig, seeded_repo: InMemoryEventRepository) -> TestClient:
    """Provide a TestClient instance with seeded repository and configuration."""
    vision = CascadeVisionAdapter()
    app = create_app(
        config=test_config,
        repository=seeded_repo,
        vision_adapter=vision,
    )
    return TestClient(app)


def test_list_events_endpoint_returns_json_list(client: TestClient) -> None:
    """TR-1: Verify GET /api/events returns list of events and total count."""
    response = client.get("/api/events")
    assert response.status_code == 200 and len(response.json()["events"]) == 2


def test_list_events_filtering_by_camera_and_category(client: TestClient) -> None:
    """TR-2: Verify GET /api/events filters correctly by camera_id."""
    response = client.get("/api/events?camera_id=front-porch")
    assert response.status_code == 200
    data = response.json()
    assert all(e["camera_id"] == "front-porch" for e in data["events"])
    assert len(data["events"]) == 1


def test_get_single_event_by_id(client: TestClient) -> None:
    """TR-3: Verify GET /api/events/{event_id} returns single event details."""
    response = client.get("/api/events/event-1")
    assert response.status_code == 200 and response.json()["id"] == "event-1"


def test_get_missing_event_returns_404(client: TestClient) -> None:
    """TR-4: Verify GET /api/events/missing returns 404 Not Found."""
    response = client.get("/api/events/non-existent-event")
    assert response.status_code == 404


def test_delete_event_endpoint(client: TestClient) -> None:
    """TR-5: Verify DELETE /api/events/{event_id} removes event."""
    response = client.delete("/api/events/event-2")
    assert response.status_code == 200 and response.json()["deleted"] is True

    # Confirm deletion
    get_res = client.get("/api/events/event-2")
    assert get_res.status_code == 404


def test_list_cameras_endpoint(client: TestClient) -> None:
    """TR-6: Verify GET /api/cameras returns configured cameras."""
    response = client.get("/api/cameras")
    assert response.status_code == 200
    assert len(response.json()["cameras"]) >= 1


def test_runtime_ai_mode_get_and_post_toggle(client: TestClient) -> None:
    """TR-7: Verify GET and POST /api/settings/ai-mode dynamically switches runtime AI cascade."""
    # Check default
    res_get = client.get("/api/settings/ai-mode")
    assert res_get.status_code == 200

    # Toggle to local_only
    res_post = client.post("/api/settings/ai-mode", json={"mode": "local_only"})
    assert res_post.status_code == 200
    assert res_post.json()["active_mode"] == "local_only"

    # Verify query reflects new mode
    res_verify = client.get("/api/settings/ai-mode")
    assert res_verify.status_code == 200
    assert res_verify.json()["active_mode"] == "local_only"


def test_face_gallery_upload_and_list(client: TestClient) -> None:
    """TR-8: Verify face upload and face gallery listing."""
    file_content = b"fake-jpg-binary-content-for-testing"
    files = {"file": ("alice_test.jpg", BytesIO(file_content), "image/jpeg")}
    data = {"resident_id": "alice"}

    upload_res = client.post("/api/settings/faces/upload", files=files, data=data)
    assert upload_res.status_code == 200

    list_res = client.get("/api/settings/faces")
    assert list_res.status_code == 200
    data = list_res.json()
    assert "alice_test.jpg" in [f["filename"] for f in data["faces"]]


def test_entities_api_endpoints_crud(client: TestClient) -> None:
    """TR-23: Verify entities CRUD endpoints."""
    create_res = client.post(
        "/api/entities",
        json={
            "id": "civic-test-1",
            "name": "Silver Honda Civic",
            "entity_type": "vehicle",
            "attributes": {"color": "silver", "make": "Honda", "model": "Civic"},
        },
    )
    assert create_res.status_code == 200

    response = client.get("/api/entities")
    assert response.status_code == 200 and len(response.json()["entities"]) >= 1
    assert any(e["id"] == "civic-test-1" for e in response.json()["entities"])

    get_res = client.get("/api/entities/civic-test-1")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Silver Honda Civic"

    del_res = client.delete("/api/entities/civic-test-1")
    assert del_res.status_code == 200


def test_enrich_event_endpoint(client: TestClient) -> None:
    """Verify POST /api/events/{event_id}/enrich runs Pass 2 clip enrichment."""
    sample_frames = [
        "dog pet squatting elimination lawn",
        "dog pet squatting elimination lawn",
    ]
    response = client.post(
        "/api/events/event-2/enrich",
        json={"frames": sample_frames},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "event-2"
    assert data["category"] == "pet_elimination"
    assert data["metadata"]["sustained_elimination"] is True

    res_404 = client.post("/api/events/non-existent-event/enrich", json={"frames": []})
    assert res_404.status_code == 404
