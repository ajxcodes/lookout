"""Integration tests for provider status and test connection endpoints."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig


@pytest.fixture
def client() -> TestClient:
    config = LookoutConfig(system_name="Provider Status Test")
    app = create_app(config=config)
    return TestClient(app)


def test_provider_status_endpoint(client: TestClient) -> None:
    """TR-22: Provider status endpoint returns 200."""
    response = client.get("/api/providers/status")
    assert response.status_code == 200


def test_provider_test_connection_endpoint(client: TestClient) -> None:
    """TR-23: Provider test connection returns reachable True."""
    response = client.post("/api/auth/test-connection", json={"provider": "go2rtc"})
    assert response.json()["reachable"] is True
