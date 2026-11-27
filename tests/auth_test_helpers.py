"""Shared fakes and injection helpers for the real-auth test suite.

No test in this suite requires the ``ring-auth-cli`` binary, live network, or
real cloud credentials.  Fake issuers are injected directly into every driver
instance held by the three route-family registries.  The Ring helper fake is a
plain Python stub invoked as ``[sys.executable, script]`` -- no Node required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig
from lookout.providers.wyze.authenticator import WyzeChallenge, WyzeLoginStart

RING_ISSUED_TOKEN = (
    "eyJhbGciOiJkaXRjIiwidHlwZSI6IkpXVCJ9.eyJydCI6InJlYWwtcmluZy1ydC0xMjM0NTY3ODkwIn0"
)
WYZE_ISSUED_ACCESS = "real-wyze-access-token-abcdefghijklmnop"
WYZE_ISSUED_REFRESH = "real-wyze-refresh-token-qrstuvwxyz012345"


def make_fake_ring_helper(
    tmp_path: Path,
    token: str,
    *,
    log_path: Path | None = None,
    fail: bool = False,
) -> Path:
    """Write a Python stub for ``RingAuthCliIssuer`` to invoke instead of the Node helper.

    The script reads the three injected environment variables, optionally
    records them to ``log_path`` so the test can assert their delivery, then
    prints a fake dotted token.  When ``fail`` is set it writes a generic stderr
    message and exits non-zero.  No credential value appears in its output.
    """
    log_value = str(log_path) if log_path is not None else None
    script = tmp_path / "fake_ring_auth_helper.py"
    script.write_text(
        "import os\n"
        "import sys\n"
        f"TOKEN = {token!r}\n"
        f"LOG = {log_value!r}\n"
        f"FAIL = {fail!r}\n"
        "if LOG is not None:\n"
        "    with open(LOG, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('email=' + os.environ.get('LOOKOUT_RING_EMAIL', ''))\n"
        "        handle.write('\\npassword=' + os.environ.get('LOOKOUT_RING_PASSWORD', ''))\n"
        "        handle.write('\\ncode=' + os.environ.get('LOOKOUT_RING_2FA_CODE', ''))\n"
        "        handle.write('\\n')\n"
        "if FAIL:\n"
        "    sys.stderr.write('fake helper failure\\n')\n"
        "    sys.exit(1)\n"
        "sys.stdout.write(TOKEN)\n",
        encoding="utf-8",
    )
    return script


class FakeRingIssuer:
    def __init__(self, token: str = RING_ISSUED_TOKEN) -> None:
        self.token = token
        self.begin_calls = 0
        self.complete_calls = 0
        self.disposed: list[str] = []
        self.last_email: str | None = None

    def begin_login(self, email: str, password: str) -> str:
        self.begin_calls += 1
        self.last_email = email
        return "fake-ring-issuer-session"

    def complete(self, session_id: str, code: str) -> str:
        self.complete_calls += 1
        return self.token

    def dispose(self, session_id: str) -> None:
        self.disposed.append(session_id)


_UNSET = object()
_DEFAULT_WYZE_CHALLENGE = WyzeChallenge(
    method="email",
    wire_mfa_type="Email",
    verification_id="fake-vid",
    destination="u***@example.com",
)


class FakeAuthTransport:
    """Queue-backed stand-in for the ``AuthServiceClient`` transport."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def api_call(
        self,
        endpoint: str,
        *,
        http_verb: str = "POST",
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        request_specific_headers: dict[str, Any] | None = None,
        nonce: Any = None,
    ) -> Any:
        self.calls.append(
            {
                "endpoint": endpoint,
                "params": dict(params or {}),
                "json": dict(json or {}),
                "headers": dict(request_specific_headers or {}),
            }
        )
        if not self.responses:
            raise RuntimeError("FakeAuthTransport ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeWyzeIssuer:
    def __init__(
        self,
        access: str = WYZE_ISSUED_ACCESS,
        refresh: str = WYZE_ISSUED_REFRESH,
        *,
        tokens: dict[str, str] | None | object = _UNSET,
        challenge: WyzeChallenge | None | object = _UNSET,
    ) -> None:
        self.access = access
        self.refresh = refresh
        if tokens is _UNSET and challenge is _UNSET:
            self.tokens = None
            self.challenge: WyzeChallenge | None = _DEFAULT_WYZE_CHALLENGE
        elif tokens is _UNSET:
            self.tokens = None
            self.challenge = challenge  # type: ignore[assignment]
        elif challenge is _UNSET:
            self.tokens = tokens  # type: ignore[assignment]
            self.challenge = None
        else:
            self.tokens = tokens  # type: ignore[assignment]
            self.challenge = challenge  # type: ignore[assignment]
        self.begin_calls = 0
        self.complete_calls = 0
        self.resend_calls = 0
        self.disposed: list[str] = []
        self.last_email: str | None = None

    def begin_login(
        self,
        email: str,
        password: str,
        key_id: str | None = None,
        api_key: str | None = None,
    ) -> WyzeLoginStart:
        self.begin_calls += 1
        self.last_email = email
        if self.tokens is not None:
            return WyzeLoginStart(
                issuer_session_id=None,
                tokens=dict(self.tokens),
                challenge=None,
            )
        return WyzeLoginStart(
            issuer_session_id="fake-wyze-issuer-session",
            tokens=None,
            challenge=self.challenge,
        )

    def complete(self, session_id: str, code: str) -> dict[str, str]:
        self.complete_calls += 1
        return {"access_token": self.access, "refresh_token": self.refresh}

    def resend(self, session_id: str) -> str:
        self.resend_calls += 1
        return "A new code was sent."

    def dispose(self, session_id: str) -> None:
        self.disposed.append(session_id)


class ReachableNativeAdapter:
    """Minimal stand-in whose connection test always succeeds/fails on demand."""

    def __init__(self, reachable: bool = True, provider: str = "fake") -> None:
        self.reachable = reachable
        self.provider = provider
        self.calls = 0

    async def test_connection(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "reachable": self.reachable,
            "provider": self.provider,
            "devices": [],
            "error": None if self.reachable else "401 Unauthorized",
        }

    async def poll_events(self, camera_id: str, since: Any = None) -> list[dict[str, Any]]:
        return []

    async def close(self) -> None:
        return None


def _all_registries() -> list[Any]:
    from lookout.adapters.web.routes import auth as auth_routes
    from lookout.adapters.web.routes import providers as providers_routes

    registries: list[Any] = [providers_routes.registry]
    sidecar_registry = getattr(auth_routes, "registry", None)
    inner = getattr(sidecar_registry, "_registry", None)
    if inner is not None:
        registries.append(inner)
    return registries


def inject_fake_issuers(
    ring: FakeRingIssuer | None = None,
    wyze: FakeWyzeIssuer | None = None,
) -> tuple[FakeRingIssuer, FakeWyzeIssuer]:
    """Inject fresh fake issuers into every registered Ring/Wyze driver."""
    ring = ring or FakeRingIssuer()
    wyze = wyze or FakeWyzeIssuer()
    for registry in _all_registries():
        for driver in registry.list_drivers():
            pid = getattr(driver, "provider_id", "").lower()
            if pid == "ring" and hasattr(driver, "set_token_issuer"):
                driver.set_token_issuer(ring)
            elif pid == "wyze" and hasattr(driver, "set_token_issuer"):
                driver.set_token_issuer(wyze)
    return ring, wyze


def make_client(
    tmp_path: Path | None = None,
    config: LookoutConfig | None = None,
    *,
    reachable: bool = True,
    with_config_file: bool = False,
) -> tuple[TestClient, FastAPI, Path]:
    """Build a TestClient with sane, offline auth defaults."""
    base = tmp_path or Path("/tmp/opencode/lookout-auth-tests")
    base.mkdir(parents=True, exist_ok=True)
    config_path = base / "config.yaml"
    if with_config_file:
        config_path.write_text("{}\n", encoding="utf-8")
    else:
        config_path.unlink(missing_ok=True)
    app = create_app(
        config=config or LookoutConfig(),
        repository=InMemoryEventRepository(),
        config_path=config_path,
    )
    app.state.sidecar_dir = base / "sidecars"
    app.state.ring_native = ReachableNativeAdapter(reachable, "ring")
    app.state.wyze_native = ReachableNativeAdapter(reachable, "wyze")
    return TestClient(app), app, config_path
