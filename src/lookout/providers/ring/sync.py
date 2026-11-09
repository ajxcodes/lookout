"""Sidecar environment file synchronizer for ring-mqtt bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig


def sync_ring_environment(config: LookoutConfig, target_dir: Path) -> list[Path]:
    """Synchronize ring-mqtt environment file with 0o600 permissions."""
    target_dir.mkdir(parents=True, exist_ok=True)
    env_file = target_dir / "ring-mqtt.env"

    token = (
        (config.ring.refresh_token if config.ring else None)
        or (config.ring.auth_token if config.ring else None)
        or os.environ.get("RING_TOKEN")
        or os.environ.get("RINGTOKEN")
        or ""
    )

    lines = [
        f"RINGTOKEN={token}",
        "MQTTHOST=localhost",
        "MQTTPORT=1883",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    return [env_file]
