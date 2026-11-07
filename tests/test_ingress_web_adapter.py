"""Unit and integration tests for Home Assistant Ingress web adapter handling."""

from pathlib import Path

from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import IngressSimulationMiddleware, create_app
from lookout.config.schema import LookoutConfig


def test_ingress_setup_redirect_preserves_path(tmp_path: Path) -> None:
    """TR-4: Verify unconfigured app with X-Ingress-Path redirects preserving ingress prefix."""
    nonexistent_config = tmp_path / "nonexistent.yaml"
    app = create_app(
        config=None,
        repository=InMemoryEventRepository(),
        config_path=nonexistent_config,
    )
    client = TestClient(app)

    response = client.get(
        "/",
        headers={"X-Ingress-Path": "/api/hassio_ingress/test-token"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/api/hassio_ingress/test-token/setup"


def test_ingress_base_href_injection(tmp_path: Path) -> None:
    """TR-5: Verify base href tag injection with Ingress path."""
    config = LookoutConfig()
    app = create_app(
        config=config,
        repository=InMemoryEventRepository(),
        config_path=tmp_path / "config.yaml",
    )
    client = TestClient(app)

    response = client.get(
        "/",
        headers={"X-Ingress-Path": "/api/hassio_ingress/test-token"},
    )
    assert response.status_code == 200
    assert '<base href="/api/hassio_ingress/test-token/">' in response.text


def test_standalone_base_href_defaults_to_root(tmp_path: Path) -> None:
    """TR-6: Verify base href tag defaults to root in standalone mode."""
    config = LookoutConfig()
    app = create_app(
        config=config,
        repository=InMemoryEventRepository(),
        config_path=tmp_path / "config.yaml",
    )
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert '<base href="/">' in response.text


def test_ingress_simulation_middleware_path_rewrite(tmp_path: Path) -> None:
    """Verify IngressSimulationMiddleware rewrites /api/hassio_ingress/{token}/... requests."""
    assert IngressSimulationMiddleware is not None
    config = LookoutConfig()
    app = create_app(
        config=config,
        repository=InMemoryEventRepository(),
        config_path=tmp_path / "config.yaml",
    )
    client = TestClient(app)

    # Test root via ingress token
    resp = client.get("/api/hassio_ingress/token123/")
    assert resp.status_code == 200
    assert '<base href="/api/hassio_ingress/token123/">' in resp.text

    # Test health check via ingress token
    health_resp = client.get("/api/hassio_ingress/token123/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    # Test static assets via ingress token
    css_resp = client.get("/api/hassio_ingress/token123/static/css/tokens.css")
    assert css_resp.status_code == 200


def test_ingress_setup_view_base_href_injection(tmp_path: Path) -> None:
    """Verify setup_view injects base href with ingress prefix."""
    app = create_app(
        config=None,
        repository=InMemoryEventRepository(),
        config_path=tmp_path / "nonexistent.yaml",
    )
    client = TestClient(app)

    response = client.get(
        "/setup",
        headers={"X-Ingress-Path": "/api/hassio_ingress/custom-token"},
    )
    assert response.status_code == 200
    assert '<base href="/api/hassio_ingress/custom-token/">' in response.text
