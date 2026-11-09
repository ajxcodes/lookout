"""Unit and integration tests for storage telemetry and volume metrics calculation."""

from pathlib import Path

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.storage_service import StorageService
from lookout.config.schema import StorageConfig


def test_media_directory_size_calculation(tmp_path: Path) -> None:
    """TR-8: Verify recursive media directory size computation and breakdown mapping."""
    base_dir = tmp_path / "data"
    recordings_dir = base_dir / "recordings"
    snapshots_dir = base_dir / "snapshots"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # Write 2048 bytes to recordings and 1024 bytes to snapshots
    (recordings_dir / "clip1.mp4").write_bytes(b"r" * 2048)
    (snapshots_dir / "snap1.jpg").write_bytes(b"s" * 1024)

    config = StorageConfig(base_path=str(base_dir))
    service = StorageService(config=config, repository=InMemoryEventRepository())
    status = service.get_storage_status()

    assert status.media_breakdown["recordings"] == 2048 and status.media_total_bytes >= 2048
    assert status.media_breakdown["snapshots"] == 1024


def test_volume_disk_usage_telemetry(tmp_path: Path) -> None:
    """TR-9: Verify filesystem volume metrics (total, used, free, utilization percent)."""
    config = StorageConfig(base_path=str(tmp_path))
    service = StorageService(config=config, repository=InMemoryEventRepository())
    status = service.get_storage_status()

    assert status.volume_total_bytes > 0 and 0.0 <= status.volume_used_percent <= 100.0
    assert status.volume_available_bytes > 0


def test_archive_volume_telemetry(tmp_path: Path) -> None:
    """TR-10: Verify network share archive volume metrics, breakdown, and availability check."""
    base_dir = tmp_path / "local"
    archive_dir = tmp_path / "network_archive"
    (base_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (archive_dir / "recordings").mkdir(parents=True, exist_ok=True)

    (archive_dir / "recordings" / "arch1.mp4").write_bytes(b"a" * 4096)

    config = StorageConfig(
        base_path=str(base_dir),
        archive_enabled=True,
        archive_path=str(archive_dir),
        archive_max_storage_gb=50.0,
    )
    service = StorageService(config=config, repository=InMemoryEventRepository())
    status = service.get_storage_status()

    assert (
        status.archive_enabled is True
        and status.archive_available is True
        and status.archive_volume_total_bytes > 0
    )
    assert status.archive_media_breakdown["recordings"] == 4096


def test_retention_runway_estimation(tmp_path: Path) -> None:
    """TR-11: Verify retention runway returns positive hours when storage headroom exists."""
    base_dir = tmp_path / "data"
    recordings_dir = base_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    (recordings_dir / "sample.mp4").write_bytes(b"x" * 1024 * 1024)

    config = StorageConfig(base_path=str(base_dir), max_storage_gb=10.0)
    service = StorageService(config=config, repository=InMemoryEventRepository())
    status = service.get_storage_status()

    assert status.retention_runway_hours is not None and status.retention_runway_hours > 0


def test_warning_flag_when_exceeding_threshold(tmp_path: Path) -> None:
    """TR-12: Verify warning flag is raised when media quota usage breaches warning threshold."""
    base_dir = tmp_path / "data"
    recordings_dir = base_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    # 100KB file
    (recordings_dir / "heavy.mp4").write_bytes(b"h" * 100000)

    # Set quota to ~105KB so 100KB > 90%
    config = StorageConfig(
        base_path=str(base_dir),
        max_storage_gb=0.0001,  # ~107KB
        warning_threshold_percent=90.0,
    )
    service = StorageService(config=config, repository=InMemoryEventRepository())
    status = service.get_storage_status()

    assert status.is_warning is True
