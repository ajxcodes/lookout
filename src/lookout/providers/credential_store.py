"""Local credential clearing / logout store.

Clearing credentials never touches ``config.cameras``: cameras and their
settings are preserved byte-for-byte.  Only the provider's secret fields are
nulled, ``logged_out`` is set, and the generated sidecar env secrets are
redacted in place (file stays mode ``0600``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lookout.providers.config_io import atomic_write_config
from lookout.providers.credential_probe import DEFAULT_SIDECAR_DIR

RING_SECRET_FIELDS = (
    "auth_token",
    "refresh_token",
    "session_id",
    "two_factor_auth_required",
)
WYZE_SECRET_FIELDS = (
    "auth_token",
    "refresh_token",
    "password",
    "key_id",
    "api_key",
    "totp_key",
)

_SIDECAR_NAMES = {"ring": "ring-mqtt.env", "wyze": "wyze-bridge.env"}
_REDACT_KEYS = {
    "ring": {"RINGTOKEN", "RING_TOKEN"},
    "wyze": {
        "WYZE_PASSWORD",
        "WYZE_ACCESS_TOKEN",
        "WYZE_REFRESH_TOKEN",
        "WYZE_AUTH_TOKEN",
        "API_KEY",
        "WYZE_API_KEY",
        "API_ID",
        "WYZE_API_ID",
    },
}


def redact_sidecar_secrets(
    provider: str, sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR
) -> Path | None:
    """Blank the secret lines of a provider sidecar env file, keeping mode 0600."""
    path = Path(sidecar_dir) / _SIDECAR_NAMES.get(provider.lower(), f"{provider}.env")
    if not path.exists():
        return None
    keys = _REDACT_KEYS.get(provider.lower(), set())
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    redacted: list[str] = []
    for line in lines:
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in keys:
                # Remove the secret line entirely so no token bytes remain.
                continue
        redacted.append(line)
    path.write_text("\n".join(redacted) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def clear_provider_credentials(
    config: Any,
    provider: str,
    sidecar_dir: str | Path = DEFAULT_SIDECAR_DIR,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Null a provider's secrets, set ``logged_out`` and redact sidecar secrets.

    ``config.cameras`` is never read or modified.  Returns a serializable,
    token-free summary.
    """
    pid = provider.lower()
    section = getattr(config, pid, None)
    if section is None:
        return {"provider": pid, "logged_out": True, "cleared": []}

    fields = RING_SECRET_FIELDS if pid == "ring" else WYZE_SECRET_FIELDS
    cleared: list[str] = []
    for field in fields:
        if hasattr(section, field):
            value = getattr(section, field)
            if value is not None and value is not False:
                cleared.append(field)
            if field == "two_factor_auth_required":
                setattr(section, field, False)
            else:
                setattr(section, field, None)
    section.logged_out = True

    redact_sidecar_secrets(pid, sidecar_dir)

    if config_path is not None:
        atomic_write_config(config, config_path)

    return {"provider": pid, "logged_out": True, "cleared": cleared}
