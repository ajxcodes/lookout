"""Unit and integration tests for the first-run onboarding setup wizard."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from auth_test_helpers import FakeWyzeIssuer, ReachableNativeAdapter, inject_fake_issuers
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.providers.wyze.authenticator import WyzeChallenge


@pytest.fixture
def unconfigured_app(tmp_path: Path):
    """Provide an app instance pointing to a non-existent config path."""
    non_existent_config = tmp_path / "non_existent_config.yaml"
    app = create_app(
        config=None,
        repository=InMemoryEventRepository(),
        config_path=non_existent_config,
    )
    return app, non_existent_config


def test_setup_redirect_when_config_missing(unconfigured_app) -> None:
    """TR-10: Verify root request redirects with HTTP 307 to /setup when unconfigured."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/setup"


def test_setup_status_indicates_unconfigured(unconfigured_app) -> None:
    """TR-11: Verify GET /api/setup/status reports configured is False."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_setup_test_ai_connection_success(unconfigured_app) -> None:
    """TR-12: Verify POST /api/setup/test-ai validates provider connection."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-ai",
        json={"provider": "mock", "model": "mock-vision-v1"},
    )
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_setup_test_camera_stream_validation(unconfigured_app) -> None:
    """TR-13: Verify POST /api/setup/test-camera probes camera stream URL."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-camera",
        json={"stream_url": "rtsp://mock-cam/live"},
    )
    assert response.status_code == 200 and response.json()["reachable"] is True


def test_setup_test_notification_ping(unconfigured_app) -> None:
    """TR-14: Verify POST /api/setup/test-notification dispatches test ping."""
    app, _ = unconfigured_app
    client = TestClient(app)
    response = client.post(
        "/api/setup/test-notification",
        json={"target_type": "webhook", "destination": "http://localhost:8080/test-webhook"},
    )
    assert response.status_code == 200 and response.json()["sent"] is True


def test_wizard_renders_four_auth_states_without_hardcoded_credentials() -> None:
    """TR-16 (AC6): wizard handles pre_authenticated/2fa_required/authenticated/error."""
    source = (
        Path(__file__).parents[1] / "src/lookout/adapters/web/static/js/setup-wizard.js"
    ).read_text(encoding="utf-8")
    for state in ("pre_authenticated", "2fa_required", "authenticated", "error"):
        assert state in source
    assert "rotate" in source.lower()
    assert "password123" not in source
    assert "logout" in source.lower()


def test_no_totp_secret_or_api_key_fields_in_wizard() -> None:
    """TR-11 (AC4): the wizard never asks for a TOTP secret or API key/ID."""
    static = Path(__file__).parents[1] / "src/lookout/adapters/web/static"
    html = (static / "setup.html").read_text(encoding="utf-8")
    script = (static / "js/setup-wizard.js").read_text(encoding="utf-8")
    assert "cred-totp" not in html
    assert "cred-api-id" not in html
    assert "cred-api-key" not in html
    combined = (html + script).lower()
    assert "authenticator" in combined
    assert "totp_key" not in combined


def test_setup_finalize_generates_valid_yaml(unconfigured_app) -> None:
    """TR-15: Verify POST /api/setup/finalize writes valid YAML config and transitions state."""
    app, target_path = unconfigured_app
    client = TestClient(app)

    payload = {
        "system_name": "Onboarded Lookout Installation",
        "cameras": [
            {
                "id": "porch",
                "name": "Porch Camera",
                "stream_url": "rtsp://localhost:8554/porch",
                "camera_type": "generic",
            }
        ],
        "residents": [
            {
                "id": "alice",
                "name": "Alice Smith",
                "notification_targets": ["default-webhook"],
            }
        ],
        "notification_targets": [
            {
                "name": "default-webhook",
                "target_type": "webhook",
                "destination": "http://localhost:8080/alert",
            }
        ],
    }

    response = client.post("/api/setup/finalize", json=payload)
    assert response.status_code == 200
    assert target_path.exists() and "system_name" in target_path.read_text()

    # Verify that app is now configured
    status_res = client.get("/api/setup/status")
    assert status_res.status_code == 200
    assert status_res.json()["configured"] is True


def _wizard_payload() -> dict[str, Any]:
    """Approximate the wizard's first-run ``configData`` body (no ``wyze`` key)."""
    return {
        "system_name": "Lookout",
        "residents": [],
        "pets": [],
        "cameras": [],
        "notification_targets": [
            {
                "name": "default-target",
                "target_type": "webhook",
                "destination": "http://localhost:8080/webhook",
            }
        ],
        "storage": {
            "base_path": "./data",
            "database_path": "./data/lookout.db",
        },
        "ring": {
            "auth_token": None,
            "api_url": "https://api.ring.com",
            "timeout_seconds": 15.0,
        },
    }


def _first_run_app(tmp_path: Path) -> tuple[TestClient, Any, Path]:
    """Build an unconfigured app with an offline, reachable native adapter."""
    target_path = tmp_path / "config.yaml"
    app = create_app(
        config=None,
        repository=InMemoryEventRepository(),
        config_path=target_path,
    )
    app.state.sidecar_dir = tmp_path / "sidecars"
    app.state.wyze_native = ReachableNativeAdapter(provider="wyze")
    return TestClient(app), app, target_path


@pytest.mark.parametrize(
    ("method", "wire_mfa_type", "access", "refresh"),
    [
        ("email", "Email", "EMAIL_A", "EMAIL_R"),
        ("sms", "PrimaryPhone", "SMS_A", "SMS_R"),
        ("totp", "TotpVerificationCode", "TOTP_A", "TOTP_R"),
    ],
)
def test_first_run_two_factor_token_survives_finalize(
    tmp_path: Path, method: str, wire_mfa_type: str, access: str, refresh: str
) -> None:
    """AC1/AC2/AC3: a step-2 token reaches ``app.state`` and ``config.yaml``."""
    issuer = FakeWyzeIssuer(
        access=access,
        refresh=refresh,
        challenge=WyzeChallenge(
            method=method,
            wire_mfa_type=wire_mfa_type,
            verification_id=f"{method}-vid",
            destination="u***@example.com",
        ),
    )
    inject_fake_issuers(wyze=issuer)
    client, app, target_path = _first_run_app(tmp_path)

    login = client.post(
        "/api/auth/wyze/login",
        json={"email": "u@example.com", "password": "pw"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["status"] == "2fa_required"
    assert body["method"] == method

    verify = client.post(
        "/api/auth/wyze/verify",
        json={"session_id": body["session_id"], "code": "654321"},
    )
    assert verify.json()["status"] == "authenticated", verify.text
    assert app.state.config.wyze.auth_token == access

    finalize = client.post("/api/setup/finalize", json=_wizard_payload())
    assert finalize.status_code == 200, finalize.text
    assert app.state.config.wyze.auth_token == access
    saved = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    assert saved["wyze"]["auth_token"] == access
    assert saved["wyze"]["refresh_token"] == refresh


def test_first_run_no_2fa_token_survives_finalize(tmp_path: Path) -> None:
    """AC5: a no-2FA token issued at login also reaches ``config.yaml``."""
    issuer = FakeWyzeIssuer(
        tokens={"access_token": "NO2FA_A", "refresh_token": "NO2FA_R"},
        challenge=None,
    )
    inject_fake_issuers(wyze=issuer)
    client, app, target_path = _first_run_app(tmp_path)

    login = client.post(
        "/api/auth/wyze/login",
        json={"email": "u@example.com", "password": "pw"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["status"] == "authenticated"
    assert app.state.config.wyze.auth_token == "NO2FA_A"
    assert issuer.complete_calls == 0

    finalize = client.post("/api/setup/finalize", json=_wizard_payload())
    assert finalize.status_code == 200, finalize.text
    assert app.state.config.wyze.auth_token == "NO2FA_A"
    saved = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    assert saved["wyze"]["auth_token"] == "NO2FA_A"
    assert saved["wyze"]["refresh_token"] == "NO2FA_R"


def test_finalize_keeps_explicit_body_credentials(tmp_path: Path) -> None:
    """A body that supplies provider credentials overrides the live values."""
    issuer = FakeWyzeIssuer(
        tokens={"access_token": "NO2FA_A", "refresh_token": "NO2FA_R"},
        challenge=None,
    )
    inject_fake_issuers(wyze=issuer)
    client, app, target_path = _first_run_app(tmp_path)
    client.post(
        "/api/auth/wyze/login",
        json={"email": "u@example.com", "password": "pw"},
    )

    payload = _wizard_payload()
    payload["wyze"] = {
        "auth_token": "EXPLICIT_A",
        "refresh_token": "EXPLICIT_R",
        "email": "explicit@example.com",
    }
    finalize = client.post("/api/setup/finalize", json=payload)
    assert finalize.status_code == 200, finalize.text
    assert app.state.config.wyze.auth_token == "EXPLICIT_A"
    saved = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    assert saved["wyze"]["auth_token"] == "EXPLICIT_A"
