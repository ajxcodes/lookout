"""AC1/AC2 - real issuance through injectable fake issuers (no CLI, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from auth_test_helpers import (
    RING_ISSUED_TOKEN,
    WYZE_ISSUED_ACCESS,
    WYZE_ISSUED_REFRESH,
    FakeRingIssuer,
    FakeWyzeIssuer,
    inject_fake_issuers,
    make_client,
    make_fake_ring_helper,
)

from lookout.adapters.ring_native import RingAuthError
from lookout.config.schema import LookoutConfig
from lookout.providers import auth_service
from lookout.providers.base import AuthResult
from lookout.providers.ring.authenticator import RingAuthCliIssuer
from lookout.providers.ring.driver import RingProviderDriver
from lookout.providers.wyze.authenticator import WyzeChallenge
from lookout.providers.wyze.driver import WyzeProviderDriver

_MOCK_LITERALS = (
    "mock-ringtoken-12345",
    "mock-wyze-token-12345",
    "mock-wyze-refresh-token-12345",
)


async def test_ring_verify_issues_real_token_via_injected_issuer(tmp_path: Path) -> None:
    """TR-5 (AC1): the issued RINGTOKEN equals the issuer value pre-sanitize."""
    issuer = FakeRingIssuer()
    driver = RingProviderDriver(token_issuer=issuer)
    config = LookoutConfig()

    login = await auth_service.login(
        "ring",
        {"email": "ring@example.com", "password": "pw"},
        config,
        driver=driver,
        sidecar_dir=tmp_path / "sidecars",
    )
    assert login.status == "2fa_required"

    result = await auth_service.verify(
        "ring",
        str(login.session_id),
        "654321",
        config,
        driver=driver,
        sidecar_dir=tmp_path / "sidecars",
    )
    assert result["status"] == "authenticated"
    assert result["refresh_token"] == issuer.token == RING_ISSUED_TOKEN
    assert result["refresh_token"] not in _MOCK_LITERALS
    assert issuer.complete_calls == 1


async def test_wyze_login_and_2fa_return_real_tokens_via_injected_sdk(tmp_path: Path) -> None:
    """TR-6 (AC2): fake SDK access/refresh tokens are returned pre-sanitize."""
    issuer = FakeWyzeIssuer()
    driver = WyzeProviderDriver(token_issuer=issuer)
    config = LookoutConfig()

    login = await auth_service.login(
        "wyze",
        {"email": "wyze@example.com", "password": "pw", "api_key": "k", "api_id": "i"},
        config,
        driver=driver,
        sidecar_dir=tmp_path / "sidecars",
    )
    assert login.status == "2fa_required"
    assert issuer.begin_calls == 1
    assert issuer.last_email == "wyze@example.com"

    result = await auth_service.verify(
        "wyze",
        str(login.session_id),
        "123456",
        config,
        driver=driver,
        sidecar_dir=tmp_path / "sidecars",
    )
    assert result["status"] == "authenticated"
    assert result["auth_token"] == WYZE_ISSUED_ACCESS
    assert result["refresh_token"] == WYZE_ISSUED_REFRESH
    assert result["auth_token"] not in _MOCK_LITERALS


# The programmatic helper writes a single dotted wrapped token to stdout.  A
# plain Python stub (invoked via sys.executable) exercises the same boundary
# offline, with no Node and no ring-client-api installed.
_FAKE_TOKEN = "dotted.ring.refresh.token" * 3


def _fake_issuer(tmp_path: Path, helper: Path, **kwargs: Any) -> RingAuthCliIssuer:
    """Build an issuer that runs the fake helper from the test's temp dir."""
    return RingAuthCliIssuer(
        str(helper), node_path=sys.executable, cwd=str(tmp_path), timeout=15, **kwargs
    )


def test_ring_helper_issues_token_and_receives_credentials_in_env(tmp_path: Path) -> None:
    """The issued token is exactly the helper stdout; email/password/code arrive in env."""
    log_path = tmp_path / "inputs.log"
    helper = make_fake_ring_helper(tmp_path, _FAKE_TOKEN, log_path=log_path)
    issuer = _fake_issuer(tmp_path, helper)

    session_id = issuer.begin_login("ring@example.com", "super-secret-pw")
    assert issuer.complete(session_id, "654321") == _FAKE_TOKEN
    assert session_id not in issuer._sessions  # dispose() ran in finally

    assert log_path.read_text(encoding="utf-8") == (
        "email=ring@example.com\npassword=super-secret-pw\ncode=654321\n"
    )


def test_ring_helper_failure_raises_clear_error_and_disposes_session(tmp_path: Path) -> None:
    """A non-zero helper exit yields RingAuthError, no token, and drops the session."""
    helper = make_fake_ring_helper(tmp_path, _FAKE_TOKEN, fail=True)
    issuer = _fake_issuer(tmp_path, helper)

    session_id = issuer.begin_login("ring@example.com", "super-secret-pw")
    with pytest.raises(RingAuthError, match="Ring authentication failed"):
        issuer.complete(session_id, "654321")
    assert issuer._sessions == {}


def test_ring_helper_invalid_token_is_rejected_and_session_disposed(tmp_path: Path) -> None:
    """Garbage helper output never becomes a token."""
    helper = make_fake_ring_helper(tmp_path, "short")
    issuer = _fake_issuer(tmp_path, helper)

    session_id = issuer.begin_login("ring@example.com", "super-secret-pw")
    with pytest.raises(RingAuthError, match="invalid token"):
        issuer.complete(session_id, "654321")
    assert issuer._sessions == {}


def test_ring_begin_login_validates_helper_and_node_paths(tmp_path: Path) -> None:
    """A missing helper or node binary fails early with a clear RingAuthError."""
    missing_helper = tmp_path / "does-not-exist.py"
    with pytest.raises(RingAuthError, match="Ring auth helper is not available"):
        RingAuthCliIssuer(
            str(missing_helper), node_path=sys.executable, cwd=str(tmp_path)
        ).begin_login("ring@example.com", "pw")

    helper = make_fake_ring_helper(tmp_path, _FAKE_TOKEN)
    with pytest.raises(RingAuthError, match="Node runtime is not available"):
        RingAuthCliIssuer(
            str(helper), node_path=str(tmp_path / "no-node"), cwd=str(tmp_path)
        ).begin_login("ring@example.com", "pw")


def test_email_2fa_login_persists_token_end_to_end(tmp_path: Path) -> None:
    """TR-8 (AC1): an emailed code completes login and persists the token."""
    issuer = FakeWyzeIssuer(
        access="EMAIL_A",
        refresh="EMAIL_R",
        challenge=WyzeChallenge(
            method="email",
            wire_mfa_type="Email",
            verification_id="email-vid",
            destination="u***@example.com",
        ),
    )
    inject_fake_issuers(wyze=issuer)
    client, app, _ = make_client(tmp_path / "email", with_config_file=True)

    login = client.post(
        "/api/auth/wyze/login",
        json={"email": "u@example.com", "password": "pw"},
    ).json()
    assert login["method"] == "email"

    verify = client.post(
        "/api/auth/wyze/verify",
        json={"session_id": login["session_id"], "code": "654321"},
    )
    assert verify.json()["status"] == "authenticated"
    assert app.state.config.wyze.auth_token == "EMAIL_A"


def test_login_without_2fa_persists_tokens_without_verify(tmp_path: Path) -> None:
    """TR-15 (AC5): a no-2FA account is never prompted; login persists tokens."""
    issuer = FakeWyzeIssuer(
        tokens={"access_token": "NO2FA_A", "refresh_token": "NO2FA_R"},
        challenge=None,
    )
    inject_fake_issuers(wyze=issuer)
    client, app, _ = make_client(tmp_path / "no2fa", with_config_file=True)

    response = client.post(
        "/api/auth/wyze/login",
        json={"email": "u@example.com", "password": "pw"},
    )
    assert response.json()["status"] == "authenticated"
    assert app.state.config.wyze.auth_token == "NO2FA_A"
    assert issuer.complete_calls == 0


class _TokenAwareAdapter:
    """Mock native client whose held token can be stale until reset."""

    def __init__(self, section: Any, *, reachable: bool = True) -> None:
        self.config = section
        self.held_token: str | None = "OLD"
        self.reset_calls = 0
        self.reachable = reachable

    async def reset_client(self) -> None:
        self.held_token = None
        self.reset_calls += 1

    async def test_connection(self) -> dict[str, Any]:
        if self.held_token is None:
            self.held_token = self.config.auth_token
        ok = self.held_token == self.config.auth_token
        return {
            "reachable": ok and self.reachable,
            "provider": "wyze",
            "devices": [],
            "error": None if ok and self.reachable else "401 Unauthorized",
        }


class _VerifyDriver:
    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        return AuthResult(
            status="authenticated",
            provider="wyze",
            auth_token="NEW",
            refresh_token="NEWR",
            email="u@example.com",
        )


async def test_fresh_token_survives_health_check_after_client_reset() -> None:
    """TR-16 (AC6): the cached client is reset before health verification."""
    config = LookoutConfig()
    config.wyze.auth_token = "OLD"
    config.wyze.refresh_token = "OLDR"
    adapter = _TokenAwareAdapter(config.wyze)

    result = await auth_service.verify(
        "wyze",
        "session",
        "654321",
        config,
        driver=_VerifyDriver(),
        request_state=SimpleNamespace(wyze_native=adapter),
        sync_env=False,
    )
    assert adapter.reset_calls >= 1
    assert config.wyze.auth_token == "NEW"
    assert result.status == "authenticated"


async def test_unhealthy_adapter_still_rolls_back_after_reset() -> None:
    """TR-17 (AC6): an unhealthy fresh token is rolled back to the old value."""
    config = LookoutConfig()
    config.wyze.auth_token = "OLD"
    config.wyze.refresh_token = "OLDR"
    adapter = _TokenAwareAdapter(config.wyze, reachable=False)

    result = await auth_service.verify(
        "wyze",
        "session",
        "654321",
        config,
        driver=_VerifyDriver(),
        request_state=SimpleNamespace(wyze_native=adapter),
        sync_env=False,
    )
    assert config.wyze.auth_token == "OLD"
    assert result.status == "error"


@pytest.mark.parametrize("prefix", ["/api/auth/wyze", "/api/providers/wyze"])
def test_resend_accepts_session_only_payload(tmp_path: Path, prefix: str) -> None:
    """Resend takes only a session id and returns a re-prompt, never a 422."""
    issuer = FakeWyzeIssuer()
    inject_fake_issuers(wyze=issuer)
    client, _app, _ = make_client(
        tmp_path / prefix.strip("/").replace("/", "-"), with_config_file=True
    )

    login = client.post(
        f"{prefix}/login",
        json={"email": "u@example.com", "password": "pw"},
    ).json()
    assert login["status"] == "2fa_required"

    resend = client.post(f"{prefix}/resend", json={"session_id": login["session_id"]})
    assert resend.status_code == 200, resend.text
    body = resend.json()
    assert body["status"] == "2fa_required"
    assert body["message"]
    assert issuer.resend_calls == 1


@pytest.mark.parametrize("prefix", ["/api/auth/wyze", "/api/providers/wyze"])
def test_resend_unknown_session_returns_error_not_validation_error(
    tmp_path: Path, prefix: str
) -> None:
    """An expired/unknown session yields an error result, not a 422 body."""
    inject_fake_issuers()
    client, _app, _ = make_client(
        tmp_path / prefix.strip("/").replace("/", "-"), with_config_file=True
    )

    resend = client.post(f"{prefix}/resend", json={"session_id": "missing-session"})
    assert resend.status_code == 200, resend.text
    assert resend.json()["status"] == "error"
