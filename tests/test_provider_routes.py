"""Integration tests for provider REST routes and dynamic sub-router mounting."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig


@pytest.fixture
def client() -> TestClient:
    config = LookoutConfig(
        system_name="Provider Routes Test",
        wyze=WyzeConfig(email="user@wyze.com"),
        ring=RingConfig(email="user@ring.com"),
    )
    app = create_app(config=config, repository=InMemoryEventRepository())
    return TestClient(app)


def test_providers_manifest_endpoint(client: TestClient) -> None:
    """TR-13: Verify /api/providers returns installed provider manifests."""
    response = client.get("/api/providers")
    assert response.status_code == 200 and len(response.json()["providers"]) >= 2
    prov_ids = [p["id"] for p in response.json()["providers"]]
    assert "wyze" in prov_ids and "ring" in prov_ids


def test_dynamic_provider_subrouter_login_and_verify(client: TestClient) -> None:
    """TR-14: Verify dynamic sub-router mounted under /api/providers/{provider}."""
    # Test sub-router manifest
    wyze_m = client.get("/api/providers/wyze/manifest")
    assert wyze_m.status_code == 200
    assert wyze_m.json()["id"] == "wyze"

    # Test login
    login_res = client.post(
        "/api/providers/wyze/login",
        json={"email": "wyze_user@example.com", "password": "pass"},
    )
    assert login_res.status_code == 200
    session_id = login_res.json()["session_id"]

    # Test verify
    verify_res = client.post(
        "/api/providers/wyze/verify",
        json={"session_id": session_id, "code": "123456"},
    )
    assert verify_res.status_code == 200 and verify_res.json()["status"] == "authenticated"


def test_backward_compatible_auth_endpoints(client: TestClient) -> None:
    """TR-15: Verify legacy /api/auth/* endpoints remain fully operational."""
    auth_res = client.get("/api/auth/providers")
    status_res = client.get("/api/auth/status")
    assert (
        auth_res.status_code == 200 and "wyze" in status_res.json() and "ring" in status_res.json()
    )
