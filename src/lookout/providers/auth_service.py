"""Single source of truth for Ring/Wyze authentication flows.

All three route families delegate here.  The service owns:

* skip-when-configured detection (config / env / sidecar) via ``credential_probe``;
* issuance through the provider driver's injected token issuer;
* Ring ``pnc``-preserving token rotation and atomic config writes;
* pre-success health verification with rollback on failure;
* logout (local credential clearing) with camera invariance;
* response sanitisation so no token value ever reaches an API response.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lookout.config.schema import RingConfig, WyzeConfig
from lookout.providers.base import AuthResult
from lookout.providers.config_io import atomic_write_config, rollback_config
from lookout.providers.credential_probe import DEFAULT_SIDECAR_DIR, credentials_present
from lookout.providers.credential_store import clear_provider_credentials

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {
    "auth_token",
    "refresh_token",
    "password",
    "totp_key",
    "api_key",
    "key_id",
    "client_secret",
    "access_token",
}
_SENSITIVE_HINTS = ("token", "secret", "password")


def sanitize_auth_result(result: Any) -> dict[str, Any]:
    """Return a token-free, JSON-serializable auth result."""
    if hasattr(result, "model_dump"):
        data = result.model_dump()
    elif isinstance(result, dict):
        data = dict(result)
    else:
        return {}
    clean: dict[str, Any] = {}
    for key, value in data.items():
        lower = str(key).lower()
        if lower in _SENSITIVE_KEYS or any(hint in lower for hint in _SENSITIVE_HINTS):
            continue
        clean[key] = value
    return clean


def _resolve_driver(provider_id: str, registry: Any | None) -> Any:
    if registry is None:
        raise RuntimeError("No provider registry available for authentication")
    getter = getattr(registry, "get_driver", None) or getattr(registry, "get_provider", None)
    if getter is None:
        raise RuntimeError("Provider registry cannot resolve drivers")
    return getter(provider_id)


def _provider_email(config: Any | None, provider_id: str) -> str | None:
    section = getattr(config, provider_id.lower(), None) if config is not None else None
    return getattr(section, "email", None) if section is not None else None


def login(
    provider_id: str,
    credentials: dict[str, Any],
    config: Any | None,
    *,
    driver: Any | None = None,
    registry: Any | None = None,
    force: bool = False,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
    environ: Mapping[str, str] | None = None,
) -> AuthResult:
    """Start a login, or skip when valid credentials already exist.

    ``force=True`` (the Rotate action) always invokes the issuer.
    """
    pid = str(provider_id).lower()
    if not force:
        probe = credentials_present(pid, config, environ, sidecar_dir)
        if probe["present"] and probe["valid"]:
            return AuthResult(
                status="pre_authenticated",
                provider=pid,
                email=_provider_email(config, pid),
                source=probe["source"],
                message="Provider is already authenticated; use Rotate to re-authenticate",
            )

    active_driver = driver or _resolve_driver(pid, registry)
    result = active_driver.authenticate(credentials)
    if not isinstance(result, AuthResult):
        result = AuthResult.model_validate(result)
    return result


def _sync_live_config(request_state: Any | None, provider_id: str, section: Any) -> None:
    """Point live native adapters at the same (mutated) config object."""
    if request_state is None:
        return
    adapter = getattr(request_state, f"{provider_id}_native", None)
    if adapter is not None and hasattr(adapter, "config"):
        with contextlib.suppress(Exception):
            adapter.config = section


def _resync_sidecar_env(config: Any, provider_id: str, sidecar_dir: str | Path) -> None:
    """Rewrite a provider sidecar ``.env`` from the current config (best effort).

    Called after a successful apply *and* after a rollback so a failed rotation
    can never leave fresh tokens in the sidecar while ``config.yaml`` holds the
    old ones (AC6/AC5).  Sync failures never block the auth flow.
    """
    try:
        if provider_id == "ring":
            from lookout.providers.ring.sync import sync_ring_environment

            sync_ring_environment(config, Path(sidecar_dir))
        else:
            from lookout.providers.wyze.sync import sync_wyze_environment

            sync_wyze_environment(config, Path(sidecar_dir))
    except Exception as exc:  # noqa: BLE001 - sidecar sync must not block a login
        logger.warning("Sidecar environment sync failed for %s: %s", provider_id, exc)


def apply_credentials(
    provider_id: str,
    config: Any,
    *,
    auth_token: str | None = None,
    refresh_token: str | None = None,
    email: str | None = None,
    request_state: Any | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    sync_env: bool = True,
) -> Path | None:
    """Apply issued credentials to ``config`` and persist atomically.

    For Ring, the existing token envelope is decoded and its ``pnc`` block is
    preserved while only ``rt``/``hid`` are replaced.  Returns the backup path
    so callers can roll back if post-success verification fails.
    """
    pid = str(provider_id).lower()
    if pid == "ring":
        from lookout.adapters.ring_native import RingAuthError, decode_ring_token, encode_ring_token

    if pid == "ring":
        section = getattr(config, "ring", None)
        if section is None:
            section = RingConfig()
            config.ring = section
        raw = refresh_token or auth_token
        if not raw:
            raise ValueError("Ring issuer did not return a token")
        try:
            inner = decode_ring_token(str(section.refresh_token or section.auth_token or ""))
        except RingAuthError:
            inner = {}
        try:
            new_inner = decode_ring_token(str(raw))
        except RingAuthError:
            new_inner = {}
        merged = dict(inner)
        merged["rt"] = new_inner.get("rt", str(raw))
        new_hid = new_inner.get("hid") or inner.get("hid")
        if new_hid:
            merged["hid"] = new_hid
        else:
            merged.pop("hid", None)
        # ``pnc`` (and any other existing fields) are intentionally untouched.
        new_raw = encode_ring_token(merged)
        section.auth_token = new_raw
        section.refresh_token = new_raw
        if email:
            section.email = email
        section.logged_out = False
        section.session_id = None
        section.two_factor_auth_required = False
    elif pid == "wyze":
        section = getattr(config, "wyze", None)
        if section is None:
            section = WyzeConfig()
            config.wyze = section
        section.auth_token = auth_token
        section.refresh_token = refresh_token
        if email:
            section.email = email
        section.logged_out = False
    else:
        raise ValueError(f"Unsupported authentication provider '{provider_id}'")

    _sync_live_config(request_state, pid, section)

    if sync_env:
        _resync_sidecar_env(config, pid, sidecar_dir)

    if config_path is not None:
        return atomic_write_config(config, config_path)
    return None


async def _verify_healthy(provider_id: str, request_state: Any | None) -> bool:
    """Confirm the native adapter is reachable and the poller is not failing."""
    if request_state is None:
        return True
    adapter = getattr(request_state, f"{provider_id}_native", None)
    if adapter is not None:
        check = getattr(adapter, "test_connection", None)
        if callable(check):
            try:
                result = check()
                if inspect.isawaitable(result):
                    result = await result
                if (
                    isinstance(result, dict)
                    and "reachable" in result
                    and not bool(result.get("reachable"))
                ):
                    return False
            except Exception:  # noqa: BLE001 - a failed probe is an unhealthy state
                return False
    poller = getattr(request_state, "native_event_poller", None)
    if poller is not None:
        health = getattr(poller, "health", None)
        if isinstance(health, dict) and int(health.get("consecutive_failures", 0)) != 0:
            return False
    return True


async def verify(
    provider_id: str,
    session_id: str,
    code: str,
    config: Any,
    *,
    driver: Any | None = None,
    registry: Any | None = None,
    request_state: Any | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    sync_env: bool = True,
) -> AuthResult:
    """Complete issuance, apply credentials and verify before reporting success."""
    pid = str(provider_id).lower()
    active_driver = driver or _resolve_driver(pid, registry)
    result = active_driver.verify_2fa(session_id, code)
    if not isinstance(result, AuthResult):
        result = AuthResult.model_validate(result)
    if result.status != "authenticated":
        return result

    section = getattr(config, pid, None)
    snapshot = section.model_dump() if section is not None else None
    backup: Path | None = None
    try:
        backup = apply_credentials(
            pid,
            config,
            auth_token=result.auth_token,
            refresh_token=result.refresh_token,
            email=result.email,
            request_state=request_state,
            sidecar_dir=sidecar_dir,
            config_path=config_path,
            environ=environ,
            sync_env=sync_env,
        )
        if not await _verify_healthy(pid, request_state):
            raise RuntimeError("post-authentication health verification failed")
    except Exception as exc:  # noqa: BLE001 - always roll back cleanly
        logger.warning("Post-authentication verification failed for %s: %s", pid, exc)
        if section is not None and snapshot is not None:
            for field, value in snapshot.items():
                if hasattr(section, field):
                    setattr(section, field, value)
        if config_path is not None:
            rollback_config(backup, config_path)
        if sync_env:
            # Re-sync the sidecar from the restored config so a failed rotation
            # cannot strand new tokens there while config.yaml holds the old ones.
            _resync_sidecar_env(config, pid, sidecar_dir)
        return AuthResult(
            status="error",
            provider=pid,
            message="Authentication could not be verified; changes were rolled back",
        )

    poller = getattr(request_state, "native_event_poller", None)
    if poller is not None and hasattr(poller, "resume"):
        poller.resume(pid)
    return result


def logout(
    provider_id: str,
    config: Any,
    *,
    driver: Any | None = None,
    registry: Any | None = None,
    request_state: Any | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
    config_path: str | Path | None = None,
) -> AuthResult:
    """Clear a provider's credentials locally (idempotent, camera-safe)."""
    pid = str(provider_id).lower()
    active_driver = driver or _resolve_driver(pid, registry)
    for session_id in list(getattr(active_driver, "_sessions", {})):
        disposer = getattr(active_driver, "dispose_session", None)
        if callable(disposer):
            disposer(session_id)
        else:
            active_driver._sessions.pop(session_id, None)

    clear_provider_credentials(config, pid, sidecar_dir, config_path)

    poller = getattr(request_state, "native_event_poller", None)
    if poller is not None and hasattr(poller, "pause"):
        poller.pause(pid)

    return AuthResult(status="logged_out", provider=pid)
