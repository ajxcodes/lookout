"""Unit tests for temporary media manager and scratch workspace governance."""

import os
import time
from pathlib import Path

import pytest

from lookout.adapters.tempfile import TempMediaContext, TempMediaManager


def test_temp_media_manager_tmpfs_scratch_with_fallback(tmp_path: Path) -> None:
    """TR-16: Verify fallback to standard directory when preferred tmpfs is unavailable."""
    manager = TempMediaManager(
        preferred_temp_dir="/nonexistent/dev/shm_invalid",
        fallback_temp_dir=str(tmp_path),
    )
    temp_dir = manager.create_temp_dir(prefix="test_")
    assert temp_dir.exists()
    assert manager.cleanup_path(temp_dir) is True
    assert not temp_dir.exists()


def test_temp_media_context_auto_cleanup(tmp_path: Path) -> None:
    """TR-17: Verify TempMediaContext purges isolated scratch directory upon context exit."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    with TempMediaContext(manager=manager) as context_dir:
        assert context_dir.exists()
        scratch_file = context_dir / "intermediate.mp4"
        scratch_file.write_bytes(b"temp-data")
        assert scratch_file.exists()

    assert not context_dir.exists()


@pytest.mark.asyncio
async def test_temp_media_context_async_auto_cleanup(tmp_path: Path) -> None:
    """Verify async context manager auto-cleans on exit."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    async with TempMediaContext(manager=manager) as context_dir:
        assert context_dir.exists()
        (context_dir / "async.tmp").touch()

    assert not context_dir.exists()


def test_retention_and_storage_quota_cleanup(tmp_path: Path) -> None:
    """TR-18: Verify retention and storage quota pruning removes excess media files."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    target_dir = tmp_path / "recordings"
    target_dir.mkdir(parents=True, exist_ok=True)

    file_old = target_dir / "old_clip.mp4"
    file_new = target_dir / "new_clip.mp4"

    file_old.write_bytes(b"X" * 2048)
    file_new.write_bytes(b"Y" * 2048)

    # Set older mtime on file_old (e.g. 10 days ago)
    old_time = time.time() - (10 * 86400)
    os.utime(file_old, (old_time, old_time))

    # Retention limit of 7 days should purge file_old
    removed_count = manager.enforce_retention_and_storage_limit(
        target_dir, retention_days=7, max_storage_bytes=10000
    )
    assert removed_count >= 1
    assert not file_old.exists()
    assert file_new.exists()


def test_storage_quota_size_limit_cleanup(tmp_path: Path) -> None:
    """Verify quota pruning removes oldest files when exceeding byte budget."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    target_dir = tmp_path / "quota_dir"
    target_dir.mkdir(parents=True, exist_ok=True)

    f1 = target_dir / "clip_1.mp4"
    f2 = target_dir / "clip_2.mp4"
    f3 = target_dir / "clip_3.mp4"

    f1.write_bytes(b"1" * 1000)
    time.sleep(0.01)
    f2.write_bytes(b"2" * 1000)
    time.sleep(0.01)
    f3.write_bytes(b"3" * 1000)

    # Total = 3000 bytes. Limit to 1500 bytes -> should delete f1 and f2
    removed_count = manager.enforce_retention_and_storage_limit(target_dir, max_storage_bytes=1500)
    assert removed_count >= 1
    assert not f1.exists()
