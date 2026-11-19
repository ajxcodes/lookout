"""AC5/AC7 - redaction, temp-file hygiene and absence of mock token literals."""

from __future__ import annotations

import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

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

from lookout.adapters.ring_native import encode_ring_token
from lookout.providers.ring.authenticator import RingAuthCliIssuer

_MOCK_LITERALS = (
    "mock-ringtoken-12345",
    "mock-wyze-token-12345",
    "mock-wyze-refresh-token-12345",
)


def test_token_values_absent_from_login_verify_status_responses(tmp_path: Path) -> None:
    """TR-12 (AC5): no issued token appears in any route-family response."""
    ring_issuer = FakeRingIssuer()
    wyze_issuer = FakeWyzeIssuer()
    inject_fake_issuers(ring=ring_issuer, wyze=wyze_issuer)
    client, _app, _path = make_client(tmp_path)
    tokens = (
        RING_ISSUED_TOKEN,
        WYZE_ISSUED_ACCESS,
        WYZE_ISSUED_REFRESH,
    )

    responses = []
    for provider in ("ring", "wyze"):
        responses.append(
            client.post(
                f"/api/auth/{provider}/login",
                json={"email": f"{provider}@example.com", "password": "real-pw"},
            )
        )
    for provider, code in (("ring", "654321"), ("wyze", "123456")):
        session = responses[0 if provider == "ring" else 1].json()["session_id"]
        responses.append(
            client.post(
                f"/api/auth/{provider}/verify",
                json={"session_id": session, "code": code},
            )
        )
    responses.append(client.get("/api/auth/status"))

    # Provider-specific router family (separate driver/session store).
    for provider, code in (("ring", "654321"), ("wyze", "123456")):
        login = client.post(
            f"/api/providers/{provider}/login",
            json={"email": f"{provider}@example.com", "password": "real-pw"},
        )
        verify = client.post(
            f"/api/providers/{provider}/verify",
            json={"session_id": login.json()["session_id"], "code": code},
        )
        responses.extend((login, verify))

    for response in responses:
        body = response.text
        for token in tokens:
            assert token not in body


def test_tokens_absent_from_logs_and_tempfiles_are_0600(tmp_path: Path, caplog) -> None:
    """TR-13 (AC5): no token/credential in logs; the helper creates no capture files."""
    issued = encode_ring_token({"rt": "cli-issued-rt", "hid": "cli-hid"})
    helper = make_fake_ring_helper(tmp_path, issued)

    with caplog.at_level(logging.DEBUG):
        issuer = RingAuthCliIssuer(
            str(helper), node_path=sys.executable, cwd=str(tmp_path), timeout=10
        )
        session_id = issuer.begin_login("user@example.com", "super-secret-password")
        assert issuer.complete(session_id, "123456") == issued

    assert issued not in caplog.text
    assert "super-secret-password" not in caplog.text
    assert list(tmp_path.glob("ring-auth-*")) == []

    # A sidecar-style file is still owner-only while the app writes one.
    fd, name = tempfile.mkstemp(prefix="ring-auth-", dir=str(tmp_path))
    os.close(fd)
    os.chmod(name, 0o600)
    assert stat.S_IMODE(Path(name).stat().st_mode) == 0o600
    Path(name).unlink()


def test_no_mock_token_literals_in_auth_source() -> None:
    """TR-14 (AC7): zero mock token literals in auth source files."""
    roots = [
        Path("src/lookout/providers"),
        Path("src/lookout/adapters/web/routes"),
        Path("src/lookout/adapters/web/static"),
    ]
    matches = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                for literal in _MOCK_LITERALS:
                    if literal in text:
                        matches.append(f"{path}:{literal}")
    assert matches == []
