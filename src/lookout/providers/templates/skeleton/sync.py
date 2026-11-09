"""Starter environment file synchronizer for skeleton provider driver."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig


def sync_skeleton_environment(config: LookoutConfig, target_dir: Path) -> list[Path]:
    """Synchronize skeleton provider environment file."""
    target_dir.mkdir(parents=True, exist_ok=True)
    env_file = target_dir / "skeleton.env"
    env_file.write_text("SKELETON_HOST=127.0.0.1\nSKELETON_PORT=8554\n", encoding="utf-8")
    env_file.chmod(0o600)
    return [env_file]
