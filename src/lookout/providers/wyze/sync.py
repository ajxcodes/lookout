"""Sidecar environment and compose file synchronizer for Wyze bridge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig


def sync_wyze_environment(config: LookoutConfig, target_dir: Path) -> list[Path]:
    """Generate wyze-bridge.env sidecar file when credentials are present."""
    if config is None or config.wyze is None:
        return []
    wyze = config.wyze
    if not (wyze.email or wyze.auth_token or wyze.refresh_token):
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    env_path = target_dir / "wyze-bridge.env"
    lines = []
    if wyze.email:
        lines.append(f"WYZE_EMAIL={wyze.email}")
    if wyze.password:
        lines.append(f"WYZE_PASSWORD={wyze.password}")
    if wyze.auth_token:
        lines.append(f"WYZE_ACCESS_TOKEN={wyze.auth_token}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
    return [env_path]
