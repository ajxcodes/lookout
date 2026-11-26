"""Atomic, owner-only configuration writes with timestamped backups.

Credential rotation and logout must never leave a partially written
``config.yaml`` behind, and the backup (which contains secret material) must
stay mode ``0600``.  This module is the single implementation of that pattern
shared by the auth service, the credential store and camera removal.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def _dump(config: Any) -> str:
    """Serialize a Pydantic config model to YAML."""
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False)


def backup_path_for(config_path: str | Path, stamp: str | None = None) -> Path:
    """Return a timestamped backup path next to ``config_path``."""
    path = Path(config_path)
    if stamp is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.bak.{stamp}")


def atomic_write_config(config: Any, config_path: str | Path) -> Path | None:
    """Atomically persist ``config`` to ``config_path``.

    Returns the backup path (when a pre-existing file was backed up), or
    ``None`` when no backup was necessary.  On any failure the previous file
    is restored from the backup before the exception is re-raised.
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _dump(config)

    backup: Path | None = None
    if path.exists():
        backup = backup_path_for(path)
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        if backup is not None and backup.exists():
            os.replace(backup, path)
        raise
    return backup


def rollback_config(backup: str | Path | None, config_path: str | Path) -> bool:
    """Restore ``config_path`` from ``backup`` (best effort)."""
    path = Path(config_path)
    if backup is None:
        return False
    source = Path(backup)
    if not source.exists():
        return False
    os.replace(source, path)
    return True
