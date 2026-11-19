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


def test_provider_status_redacts_ring_refresh_token(client: TestClient) -> None:
    """The providers status route must not leak native stream refresh tokens."""
    client.app.state.native_stream_manager.streams = {
        "front_door": "ring:?camera_id=123&device_id=abc&refresh_token=SECRET123"
    }

    response = client.get("/api/providers/status")

    assert response.status_code == 200
    assert "SECRET123" not in response.text
    assert "refresh_token=[redacted]" in response.text
    streams = response.json()["native"]["stream_manager"]["streams"]
    assert "front_door" in streams
    assert "camera_id=123" in streams["front_door"]


def test_provider_status_redacts_bridge_refresh_token(client: TestClient) -> None:
    """The providers status route must redact bridge CLI refresh tokens."""
    client.app.state.native_stream_manager.streams = {
        "front_door": (
            "exec:node /app/ring_bridge/index.mjs --camera-id 123 --refresh-token SECRET123"
        )
    }

    response = client.get("/api/providers/status")

    assert response.status_code == 200
    assert "SECRET123" not in response.text
    assert "--refresh-token [redacted]" in response.text


def test_provider_status_preserves_stream_shape_and_fields(client: TestClient) -> None:
    """Redaction preserves the stream_manager shape and non-sensitive fields."""
    client.app.state.native_stream_manager.streams = {
        "front_door": "ring:?camera_id=123&device_id=abc&refresh_token=SECRET123",
        "backyard_snapshot": ("ring:?camera_id=456&device_id=def&refresh_token=SECRET456&snapshot"),
    }

    response = client.get("/api/providers/status")

    assert response.status_code == 200
    manager = response.json()["native"]["stream_manager"]
    assert set(manager["streams"]) == {"front_door", "backyard_snapshot"}
    assert all(isinstance(v, str) for v in manager["streams"].values())
    assert isinstance(manager["running"], bool)
    assert "api_url" in manager
    assert "binary" in manager
    assert "SECRET123" not in response.text
    assert "SECRET456" not in response.text
