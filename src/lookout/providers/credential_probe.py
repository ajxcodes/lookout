"""Offline credential discovery shared by drivers, discovery and auth routes.

The probe checks ``LookoutConfig``, the process environment and the generated
sidecar ``.env`` files.  It is deliberately pure/offline: no network calls and
no logging of credential values.  A provider that has been logged out
(``<provider>.logged_out``) is reported as absent regardless of any residual
values, until a successful login clears the flag.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_SIDECAR_DIR = Path("data/sidecars")

RING_ENV_KEYS = ("RINGTOKEN", "RING_TOKEN")
WYZE_ENV_KEYS = ("WYZE_ACCESS_TOKEN", "WYZE_REFRESH_TOKEN", "WYZE_AUTH_TOKEN")


def read_sidecar_env(path: str | Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` sidecar env file without logging any values."""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_get(environ: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = environ.get(key)
        if value:
            return str(value)
    return None


def _result(present: bool, source: str, valid: bool) -> dict[str, Any]:
    """Build the discovery result; it never contains a credential value."""
    return {"present": present, "source": source, "valid": valid}


def _ring_is_valid(raw: str | None) -> bool:
    if not raw:
        return False
    from lookout.adapters.ring_native import RingAuthError, decode_ring_token

    try:
        return bool(decode_ring_token(str(raw)).get("rt"))
    except (RingAuthError, ValueError, TypeError):
        return False


def ring_credentials_present(
    config: Any | None = None,
    environ: Mapping[str, str] | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
) -> dict[str, Any]:
    """Report whether usable Ring credentials exist and where they came from.

    Sources are checked in priority order: ``config`` → ``env`` → ``sidecar``.
    ``logged_out`` short-circuits everything to ``source="logged_out"``.
    """
    environ = os.environ if environ is None else environ
    ring_cfg = getattr(config, "ring", None) if config is not None else None

    if ring_cfg is not None and bool(getattr(ring_cfg, "logged_out", False)):
        return _result(False, "logged_out", False)

    config_raw = getattr(ring_cfg, "refresh_token", None) or getattr(ring_cfg, "auth_token", None)
    if config_raw and _ring_is_valid(str(config_raw)):
        return _result(True, "config", True)

    env_raw = _env_get(environ, RING_ENV_KEYS)
    if env_raw and _ring_is_valid(env_raw):
        return _result(True, "env", True)

    sidecar = read_sidecar_env(Path(sidecar_dir) / "ring-mqtt.env")
    sidecar_raw = sidecar.get("RINGTOKEN")
    if sidecar_raw and _ring_is_valid(sidecar_raw):
        return _result(True, "sidecar", True)

    return _result(False, "none", False)


def wyze_credentials_present(
    config: Any | None = None,
    environ: Mapping[str, str] | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
) -> dict[str, Any]:
    """Report whether usable Wyze credentials exist and where they came from."""
    environ = os.environ if environ is None else environ
    wyze_cfg = getattr(config, "wyze", None) if config is not None else None

    if wyze_cfg is not None and bool(getattr(wyze_cfg, "logged_out", False)):
        return _result(False, "logged_out", False)

    if wyze_cfg is not None and (
        getattr(wyze_cfg, "auth_token", None) or getattr(wyze_cfg, "refresh_token", None)
    ):
        return _result(True, "config", True)

    if _env_get(environ, WYZE_ENV_KEYS):
        return _result(True, "env", True)

    sidecar = read_sidecar_env(Path(sidecar_dir) / "wyze-bridge.env")
    if sidecar.get("WYZE_ACCESS_TOKEN") or sidecar.get("WYZE_REFRESH_TOKEN"):
        return _result(True, "sidecar", True)

    return _result(False, "none", False)


def credentials_present(
    provider: str,
    config: Any | None = None,
    environ: Mapping[str, str] | None = None,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
) -> dict[str, Any]:
    """Dispatch to the provider-specific credential probe."""
    pid = str(provider).lower()
    if pid == "ring":
        return ring_credentials_present(config, environ, sidecar_dir)
    if pid == "wyze":
        return wyze_credentials_present(config, environ, sidecar_dir)
    return _result(False, "none", False)
