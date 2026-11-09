"""Unit and integration tests for FIFO looping retention engine and two-tier eviction."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.storage_service import StorageService
from lookout.config.schema import StorageConfig
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel
from lookout.ports.egress import MediaArchivalPort


class MockMediaArchivalAdapter(MediaArchivalPort):
    """Test spy recording on_pre_evict invocations."""

    def __init__(self) -> None:
        self.called_count = 0
        self.last_event_id: str | None = None
        self.last_files: list[Path] = []

    async def on_pre_evict(self, event: CameraEvent, file_paths: list[Path]) -> None:
        self.called_count += 1
        self.last_event_id = event.id
        self.last_files = file_paths


@pytest.mark.asyncio
async def test_fifo_eviction_purges_oldest_files_first_when_no_archive(tmp_path: Path) -> None:
    """TR-13: Verify oldest media is purged first when no secondary archive tier is enabled."""
    data_dir = tmp_path / "data"
    recordings_dir = data_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    old_clip = recordings_dir / "old.mp4"
    recent_clip = recordings_dir / "recent.mp4"
    old_clip.write_bytes(b"o" * 50000)
    recent_clip.write_bytes(b"r" * 50000)

    e_old = CameraEvent(
        id="evt-old",
        camera_id="cam-1",
        timestamp=now - timedelta(days=7),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Old event",
        media_uri=str(old_clip),
    )
    e_recent = CameraEvent(
        id="evt-recent",
        camera_id="cam-1",
        timestamp=now - timedelta(hours=2),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Recent event",
        media_uri=str(recent_clip),
    )
    await repo.save(e_old)
    await repo.save(e_recent)

    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        archive_enabled=False,
    )
    service = StorageService(config=config, repository=repo)

    result = await service.execute_fifo_eviction(target_bytes=45000)
    assert not old_clip.exists() and recent_clip.exists() and result.files_deleted == 1

    saved_old = await repo.get_by_id("evt-old")
    assert saved_old is not None
    assert saved_old.media_uri is None
    assert saved_old.metadata.get("media_purged") is True


@pytest.mark.asyncio
async def test_two_tier_archive_migration_on_local_quota_breach(tmp_path: Path) -> None:
    """TR-14: Verify local quota overflow migrates oldest clips to network archive share."""
    data_dir = tmp_path / "data"
    archive_dir = tmp_path / "network_archive"
    (data_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (archive_dir / "recordings").mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    local_clip = data_dir / "recordings" / "migrate_me.mp4"
    local_clip.write_bytes(b"m" * 60000)

    archive_clip = archive_dir / "recordings" / "migrate_me.mp4"

    event = CameraEvent(
        id="evt-migrate",
        camera_id="cam-1",
        timestamp=now - timedelta(days=3),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Visitor to migrate",
        media_uri=str(local_clip),
    )
    await repo.save(event)

    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        archive_enabled=True,
        archive_path=str(archive_dir),
    )
    service = StorageService(config=config, repository=repo)

    result = await service.execute_fifo_eviction(target_bytes=10000)
    assert not local_clip.exists() and archive_clip.exists() and result.files_migrated == 1

    saved = await repo.get_by_id("evt-migrate")
    assert saved is not None
    assert saved.metadata.get("storage_tier") == "archive"
    assert str(archive_clip) in (saved.media_uri or "")


@pytest.mark.asyncio
async def test_archive_quota_breach_purges_oldest_archived_media(tmp_path: Path) -> None:
    """TR-15: Verify exceeding archive quota purges oldest archived files permanently."""
    data_dir = tmp_path / "local"
    archive_dir = tmp_path / "archive"
    (data_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (archive_dir / "recordings").mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    old_archive_clip = archive_dir / "recordings" / "arch_old.mp4"
    recent_archive_clip = archive_dir / "recordings" / "arch_recent.mp4"
    old_archive_clip.write_bytes(b"O" * 70000)
    recent_archive_clip.write_bytes(b"R" * 70000)

    e1 = CameraEvent(
        id="evt-arch-old",
        camera_id="cam-1",
        timestamp=now - timedelta(days=20),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Old archived clip",
        media_uri=str(old_archive_clip),
        metadata={"storage_tier": "archive"},
    )
    e2 = CameraEvent(
        id="evt-arch-recent",
        camera_id="cam-1",
        timestamp=now - timedelta(days=5),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Recent archived clip",
        media_uri=str(recent_archive_clip),
        metadata={"storage_tier": "archive"},
    )
    await repo.save(e1)
    await repo.save(e2)

    # 140KB total in archive, limit is ~95KB (0.00009 GB)
    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        archive_enabled=True,
        archive_path=str(archive_dir),
        archive_max_storage_gb=0.00009,
        low_watermark_percent=90.0,
    )
    service = StorageService(config=config, repository=repo)

    result = await service.execute_fifo_eviction()
    assert (
        not old_archive_clip.exists() and recent_archive_clip.exists() and result.files_deleted >= 1
    )


@pytest.mark.asyncio
async def test_fifo_eviction_preserves_bookmarked_events_across_tiers(tmp_path: Path) -> None:
    """TR-16: Verify bookmarked clips are immune to eviction across local and archive tiers."""
    data_dir = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    (data_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (archive_dir / "recordings").mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    bookmarked_clip = data_dir / "recordings" / "saved_bm.mp4"
    unbookmarked_clip = data_dir / "recordings" / "regular.mp4"
    bookmarked_clip.write_bytes(b"B" * 50000)
    unbookmarked_clip.write_bytes(b"U" * 50000)

    e_bm = CameraEvent(
        id="evt-bm",
        camera_id="cam-1",
        timestamp=now - timedelta(days=15),
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.HIGH,
        summary="Bookmarked intruder",
        media_uri=str(bookmarked_clip),
        bookmarked=True,
    )
    e_unbm = CameraEvent(
        id="evt-unbm",
        camera_id="cam-1",
        timestamp=now - timedelta(days=2),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Regular visitor",
        media_uri=str(unbookmarked_clip),
        bookmarked=False,
    )
    await repo.save(e_bm)
    await repo.save(e_unbm)

    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        archive_enabled=False,
        preserve_bookmarked=True,
    )
    service = StorageService(config=config, repository=repo)

    await service.execute_fifo_eviction(target_bytes=40000)
    assert bookmarked_clip.exists() and not unbookmarked_clip.exists()


@pytest.mark.asyncio
async def test_events_priority_retention_mode(tmp_path: Path) -> None:
    """TR-17: Verify events_priority mode evicts routine events before critical threat events."""
    data_dir = tmp_path / "data"
    recordings_dir = data_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    critical_clip = recordings_dir / "critical.mp4"
    routine_clip = recordings_dir / "routine.mp4"
    critical_clip.write_bytes(b"C" * 50000)
    routine_clip.write_bytes(b"R" * 50000)

    # Critical threat is older than routine event
    e_crit = CameraEvent(
        id="evt-crit",
        camera_id="cam-1",
        timestamp=now - timedelta(days=10),
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.CRITICAL,
        summary="Critical breach",
        media_uri=str(critical_clip),
    )
    e_routine = CameraEvent(
        id="evt-routine",
        camera_id="cam-1",
        timestamp=now - timedelta(days=2),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.INFO,
        summary="Routine passing",
        media_uri=str(routine_clip),
    )
    await repo.save(e_crit)
    await repo.save(e_routine)

    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        retention_mode="events_priority",
        archive_enabled=False,
    )
    service = StorageService(config=config, repository=repo)

    await service.execute_fifo_eviction(target_bytes=40000)
    assert not routine_clip.exists() and critical_clip.exists()


@pytest.mark.asyncio
async def test_media_archival_pre_evict_hook_invocation(tmp_path: Path) -> None:
    """TR-18: Verify MediaArchivalPort hook is dispatched before permanent file deletion."""
    data_dir = tmp_path / "data"
    recordings_dir = data_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookout.db"
    repo = SqliteEventRepository(db_path=db_path)

    now = datetime.now(UTC)
    old_clip = recordings_dir / "evt_old.mp4"
    old_clip.write_bytes(b"X" * 40000)

    event = CameraEvent(
        id="evt-old",
        camera_id="cam-1",
        timestamp=now - timedelta(days=5),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Event to prune with hook",
        media_uri=str(old_clip),
    )
    await repo.save(event)

    mock_archival = MockMediaArchivalAdapter()
    config = StorageConfig(
        base_path=str(data_dir),
        database_path=str(db_path),
        archive_enabled=False,
    )
    service = StorageService(config=config, repository=repo, archival_adapter=mock_archival)

    await service.execute_fifo_eviction(target_bytes=10000)
    assert mock_archival.called_count == 1 and mock_archival.last_event_id == "evt-old"
