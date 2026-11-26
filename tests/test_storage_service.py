"""Tests for continuous-tier storage sweeping."""

import os
import time
from pathlib import Path

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.storage_service import StorageService
from lookout.config.schema import StorageConfig


def test_sweep_continuous_deletes_oldest_first(tmp_path: Path) -> None:
    base = tmp_path / "data"
    d = base / "continuous" / "cam1" / "2026" / "11" / "19"
    d.mkdir(parents=True, exist_ok=True)
    oldest = d / "oldest.mp4"
    newest = d / "newest.mp4"
    oldest.write_bytes(b"o" * 5000)
    time.sleep(0.05)
    newest.write_bytes(b"n" * 5000)

    config = StorageConfig(base_path=str(base), max_storage_gb=0.00001)
    service = StorageService(config=config, repository=InMemoryEventRepository())
    import asyncio

    asyncio.run(service.sweep_continuous())
    assert not oldest.exists() and newest.exists()


def test_sweep_continuous_respects_retention_days(tmp_path: Path) -> None:
    base = tmp_path / "data"
    d = base / "continuous" / "cam1" / "2026" / "11" / "19"
    d.mkdir(parents=True, exist_ok=True)
    expired_segment = d / "expired.mp4"
    fresh_segment = d / "fresh.mp4"
    expired_segment.write_bytes(b"e" * 1000)
    fresh_segment.write_bytes(b"f" * 1000)
    old_mtime = time.time() - 2 * 86400
    os.utime(expired_segment, (old_mtime, old_mtime))

    config = StorageConfig(base_path=str(base), max_storage_gb=50.0)
    service = StorageService(config=config, repository=InMemoryEventRepository())
    import asyncio

    asyncio.run(service.sweep_continuous())
    remaining = list((base / "continuous").rglob("*.mp4"))
    assert expired_segment not in remaining and fresh_segment in remaining


def test_recording_continues_under_quota_pressure(tmp_path: Path) -> None:
    base = tmp_path / "data"
    d = base / "continuous" / "cam1" / "2026" / "11" / "19"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.mp4").write_bytes(b"a" * 5000)
    (d / "b.mp4").write_bytes(b"b" * 5000)

    config = StorageConfig(base_path=str(base), max_storage_gb=0.00001)
    service = StorageService(config=config, repository=InMemoryEventRepository())
    import asyncio

    asyncio.run(service.sweep_continuous())
    assert service.get_storage_status().media_total_bytes <= service.config.max_storage_bytes
