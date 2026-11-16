"""Sidecar environment file synchronizer for ring-mqtt bridge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig


def sync_ring_environment(config: LookoutConfig, target_dir: Path) -> list[Path]:
    """Generate ring-mqtt.env sidecar file when credentials are present."""
    if config is None or config.ring is None:
        return []
    ring = config.ring
    if not (ring.email or ring.refresh_token or ring.auth_token):
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    env_path = target_dir / "ring-mqtt.env"
    lines = [
        "MQTTHOST=localhost",
    ]
    if ring.email:
        lines.append(f"RING_EMAIL={ring.email}")
    if ring.refresh_token:
        lines.append(f"RING_REFRESH_TOKEN={ring.refresh_token}")
        lines.append(f"RINGTOKEN={ring.refresh_token}")
    if ring.auth_token:
        lines.append(f"RING_TOKEN={ring.auth_token}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
    return [env_path]
