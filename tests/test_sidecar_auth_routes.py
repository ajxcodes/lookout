"""Integration tests for sidecar authentication and status REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig


@pytest.fixture
def test_client() -> TestClient:
    config = LookoutConfig(
        system_name="Route Test",
        wyze=WyzeConfig(email="user@wyze.com"),
        ring=RingConfig(email="user@ring.com"),
    )
    app = create_app(config=config, repository=InMemoryEventRepository())
    return TestClient(app)


def test_auth_status_endpoint(test_client: TestClient) -> None:
    """TR-10: Verify /api/auth/status endpoint returns provider reports."""
    response = test_client.get("/api/auth/status")
    assert response.status_code == 200 and "wyze" in response.json() and "ring" in response.json()
    assert "go2rtc" in response.json()


def test_wyze_auth_routes_flow(test_client: TestClient) -> None:
    """TR-11: Verify Wyze login and 2FA verification REST flow."""
    login_res = test_client.post(
        "/api/auth/wyze/login",
        json={"email": "wyze_test@example.com", "password": "password123"},
    )
    assert login_res.status_code == 200
    session_id = login_res.json()["session_id"]

    verify_res = test_client.post(
        "/api/auth/wyze/verify",
        json={"session_id": session_id, "code": "123456"},
    )
    assert verify_res.status_code == 200 and verify_res.json()["status"] == "authenticated"


def test_ring_auth_routes_flow(test_client: TestClient) -> None:
    """TR-12: Verify Ring login and 2FA verification REST flow."""
    login_res = test_client.post(
        "/api/auth/ring/login",
        json={"email": "ring_test@example.com", "password": "password123"},
    )
    assert login_res.status_code == 200
    session_id = login_res.json()["session_id"]

    verify_res = test_client.post(
        "/api/auth/ring/verify",
        json={"session_id": session_id, "code": "654321"},
    )
    assert verify_res.status_code == 200 and verify_res.json()["status"] == "authenticated"


def test_test_connection_endpoint(test_client: TestClient) -> None:
    """TR-13: Verify provider connection testing endpoint."""
    response = test_client.post(
        "/api/auth/test-connection",
        json={"provider": "go2rtc", "endpoint": "http://localhost:1984/test"},
    )
    assert response.status_code == 200 and response.json()["reachable"] is True


def test_auth_providers_endpoint(test_client: TestClient) -> None:
    """Verify /api/auth/providers returns list of registered providers."""
    response = test_client.get("/api/auth/providers")
    assert response.status_code == 200
    providers = [p["id"] for p in response.json()["providers"]]
    assert "wyze" in providers and "ring" in providers


def test_auth_adopt_endpoint(test_client: TestClient) -> None:
    """Verify /api/auth/adopt adopts camera stream into app config."""
    res = test_client.post(
        "/api/auth/adopt",
        json={
            "provider": "wyze",
            "stream_url": "rtsp://localhost:8554/cam1",
            "camera_id": "adopted-cam-1",
            "camera_name": "Adopted Cam 1",
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "adopted"
    assert res.json()["camera"]["id"] == "adopted-cam-1"
