"""Sidecar environment and compose file synchronizer for Wyze bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig


def sync_wyze_environment(config: LookoutConfig, target_dir: Path) -> list[Path]:
    """Synchronize Wyze bridge environment file with 0o600 permissions."""
    target_dir.mkdir(parents=True, exist_ok=True)
    env_file = target_dir / "wyze-bridge.env"

    email = (config.wyze.email if config.wyze else None) or os.environ.get("WYZE_EMAIL") or ""
    password = (
        (config.wyze.password if config.wyze else None) or os.environ.get("WYZE_PASSWORD") or ""
    )
    token = (
        (config.wyze.auth_token if config.wyze else None)
        or os.environ.get("WYZE_ACCESS_TOKEN")
        or ""
    )
    api_key = (
        (config.wyze.api_key if config.wyze else None)
        or os.environ.get("WYZE_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    )
    key_id = (
        (config.wyze.key_id if config.wyze else None)
        or os.environ.get("WYZE_KEY_ID")
        or os.environ.get("WYZE_API_ID")
        or os.environ.get("API_ID")
        or ""
    )

    lines = [
        f"WYZE_EMAIL={email}",
        f"WYZE_PASSWORD={password}",
        f"WYZE_ACCESS_TOKEN={token}",
        "WB_IP=0.0.0.0",
        "ON_DEMAND=True",
    ]
    if api_key:
        lines.append(f"API_KEY={api_key}")
        lines.append(f"WYZE_API_KEY={api_key}")
    if key_id:
        lines.append(f"API_ID={key_id}")
        lines.append(f"WYZE_API_ID={key_id}")

    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    return [env_file]
