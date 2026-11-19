"""AC1/AC2 - real issuance through injectable fake issuers (no CLI, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from auth_test_helpers import (
    RING_ISSUED_TOKEN,
    WYZE_ISSUED_ACCESS,
    WYZE_ISSUED_REFRESH,
    FakeRingIssuer,
    FakeWyzeIssuer,
    make_fake_ring_helper,
)

from lookout.adapters.ring_native import RingAuthError
from lookout.config.schema import LookoutConfig
from lookout.providers import auth_service
from lookout.providers.ring.authenticator import RingAuthCliIssuer
from lookout.providers.ring.driver import RingProviderDriver
from lookout.providers.wyze.authenticator import WyzeSdkIssuer
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

    login = auth_service.login(
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

    login = auth_service.login(
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


class _LateLoginWyzeClient:
    """Fake SDK client that authenticates only when ``login()`` is called."""

    def __init__(self, **kwargs: Any) -> None:
        self.login_calls = 0
        self._token: str | None = None
        self._refresh_token: str | None = None

    def login(self) -> None:
        self.login_calls += 1
        self._token = "late-login-access"
        self._refresh_token = "late-login-refresh"


class _AutoLoginWyzeClient:
    """Fake SDK client mirroring the real SDK's ``__init__`` auto-login."""

    def __init__(self, **kwargs: Any) -> None:
        self.login_calls = 0
        self._token = "auto-login-access"
        self._refresh_token = "auto-login-refresh"

    def login(self) -> None:
        self.login_calls += 1


def test_wyze_sdk_issuer_calls_login_for_unauthenticated_client() -> None:
    """A fake client with no token yet must be logged in exactly once."""
    clients: list[_LateLoginWyzeClient] = []

    def factory(**kwargs: Any) -> _LateLoginWyzeClient:
        client = _LateLoginWyzeClient(**kwargs)
        clients.append(client)
        return client

    issuer = WyzeSdkIssuer(client_factory=factory)
    session_id = issuer.begin_login("wyze@example.com", "pw", api_key="k", totp_key="seed")
    tokens = issuer.complete(session_id, "123456")

    assert tokens == {
        "access_token": "late-login-access",
        "refresh_token": "late-login-refresh",
    }
    assert len(clients) == 1
    assert clients[0].login_calls == 1
    assert session_id not in issuer._sessions  # dispose() ran in finally


def test_wyze_sdk_issuer_skips_login_for_already_authenticated_client() -> None:
    """Regression: the real SDK auto-logs-in in ``__init__``; never call login() again."""
    clients: list[_AutoLoginWyzeClient] = []

    def factory(**kwargs: Any) -> _AutoLoginWyzeClient:
        client = _AutoLoginWyzeClient(**kwargs)
        clients.append(client)
        return client

    issuer = WyzeSdkIssuer(client_factory=factory)
    session_id = issuer.begin_login("wyze@example.com", "pw", key_id="i", api_key="k")
    tokens = issuer.complete(session_id, "123456")

    assert tokens == {
        "access_token": "auto-login-access",
        "refresh_token": "auto-login-refresh",
    }
    assert len(clients) == 1
    assert clients[0].login_calls == 0
    assert session_id not in issuer._sessions  # dispose() ran in finally


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
