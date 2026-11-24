"""Real-code-path tests for the Ring legacy-session compatibility helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from lookout.adapters.ring_api_compat import (
    DEFAULT_PROFILES,
    RingClientProfile,
    apply,
    is_authz_denied,
    skip_legacy_session,
)


def test_skip_legacy_session_seeds_credential_free_sentinel() -> None:
    ring = SimpleNamespace(session=None)
    skip_legacy_session(ring)

    assert isinstance(ring.session, dict)
    assert ring.session == {"clients_api_session": "skipped"}
    assert all("token" not in key.lower() for key in ring.session)


def test_skip_legacy_session_preserves_existing_session() -> None:
    existing = {"session_id": "real-session"}
    ring = SimpleNamespace(session=existing)
    skip_legacy_session(ring)

    assert ring.session is existing


async def test_real_ring_device_query_never_uses_session_endpoint(monkeypatch: Any) -> None:
    """With the sentinel set, ring-doorbell's real client skips the 406 endpoint."""
    from ring_doorbell import Auth, Ring

    auth = Auth(
        "android:com.ringapp",
        token={"access_token": "access", "refresh_token": "refresh"},
        hardware_id="hardware-id",
    )
    ring = Ring(auth)
    skip_legacy_session(ring)

    queried: list[str] = []

    class FakeResponse:
        def json(self) -> dict[str, Any]:
            return {}

    async def fake_query(url: str, **kwargs: Any) -> FakeResponse:
        queried.append(url)
        return FakeResponse()

    monkeypatch.setattr(ring, "_async_query", fake_query)
    await ring.async_update_devices()

    assert queried == ["/clients_api/ring_devices"]
    assert "/clients_api/session" not in queried
    await auth.async_close()


def test_apply_patches_api_version_without_credentials(monkeypatch: Any) -> None:
    import ring_doorbell.const as const_module
    import ring_doorbell.ring as ring_module

    # Register the originals with monkeypatch so they are restored on teardown.
    monkeypatch.setattr(ring_module, "API_VERSION", ring_module.API_VERSION)
    monkeypatch.setattr(const_module, "API_VERSION", const_module.API_VERSION)

    apply(RingClientProfile("test", "android:com.ringapp:6.62.0", 9))

    assert str(ring_module.API_VERSION) == "9"
    assert str(const_module.API_VERSION) == "9"


def test_is_authz_denied_detects_session_rejection() -> None:
    assert is_authz_denied(
        RuntimeError(
            "HTTP error with status code 406 during query of url "
            "https://api.ring.com/clients_api/session"
        )
    )
    assert is_authz_denied(SimpleNamespace(response=SimpleNamespace(status_code=406)))
    assert not is_authz_denied(RuntimeError("network timeout"))


def test_default_profiles_remain_device_compatible() -> None:
    assert all(not p.user_agent.startswith("lookout/") for p in DEFAULT_PROFILES)
    assert all(
        p.user_agent.startswith(("android:com.ringapp", "ios:com.ringapp"))
        for p in DEFAULT_PROFILES
    )
