"""Unit tests for media retention worker and tiered expiration policies."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.sqlite.retention import MediaRetentionWorker
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@pytest.mark.asyncio
async def test_retention_purges_routine_events_after_7_days(tmp_path: Path) -> None:
    """TR-17: Verify retention worker purges routine events older than 7 days."""
    db_file = tmp_path / "retention_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    worker = MediaRetentionWorker(
        repository=repo, routine_retention_days=7, critical_retention_days=30
    )
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    # 2 routine events older than 7 days
    e_old_1 = CameraEvent(
        id="routine-old-1",
        camera_id="cam-1",
        timestamp=now - timedelta(days=8),
        category=EventCategory.FALSE_POSITIVE,
        threat_level=ThreatLevel.INFO,
        summary="Routine wind trigger",
    )
    e_old_2 = CameraEvent(
        id="routine-old-2",
        camera_id="cam-1",
        timestamp=now - timedelta(days=12),
        category=EventCategory.ANIMAL_WILDLIFE,
        threat_level=ThreatLevel.LOW,
        summary="Routine squirrel",
    )
    # 1 routine event younger than 7 days
    e_recent = CameraEvent(
        id="routine-recent",
        camera_id="cam-1",
        timestamp=now - timedelta(days=3),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Recent delivery",
    )

    await repo.save(e_old_1)
    await repo.save(e_old_2)
    await repo.save(e_recent)

    result = await worker.purge_expired(now=now)
    assert result.events_purged == 2
    assert await repo.get_by_id("routine-old-1") is None
    assert await repo.get_by_id("routine-old-2") is None
    assert await repo.get_by_id("routine-recent") is not None


@pytest.mark.asyncio
async def test_retention_preserves_critical_threats_for_30_days(tmp_path: Path) -> None:
    """TR-18: Verify retention worker preserves critical threats (< 30 days old)."""
    db_file = tmp_path / "retention_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    worker = MediaRetentionWorker(
        repository=repo, routine_retention_days=7, critical_retention_days=30
    )
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    # Critical threat 15 days old (> 7d routine limit, but < 30d critical limit)
    e_threat = CameraEvent(
        id="threat-15d",
        camera_id="cam-1",
        timestamp=now - timedelta(days=15),
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.HIGH,
        summary="Intruder at midnight",
    )
    # Critical threat 25 days old
    e_critical = CameraEvent(
        id="critical-25d",
        camera_id="cam-1",
        timestamp=now - timedelta(days=25),
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.CRITICAL,
        summary="Forced entry attempt",
    )

    await repo.save(e_threat)
    await repo.save(e_critical)

    result = await worker.purge_expired(now=now)
    assert result.events_purged == 0
    assert await repo.get_by_id("threat-15d") is not None
    assert await repo.get_by_id("critical-25d") is not None


@pytest.mark.asyncio
async def test_retention_deletes_physical_media_files(tmp_path: Path) -> None:
    """TR-19: Verify retention worker deletes physical media files when events expire."""
    db_file = tmp_path / "retention_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    worker = MediaRetentionWorker(
        repository=repo, routine_retention_days=7, critical_retention_days=30
    )
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    clip_path = tmp_path / "test_clip.mp4"
    clip_path.write_bytes(b"0" * 1024)
    assert clip_path.exists()

    event = CameraEvent(
        id="event-with-clip",
        camera_id="cam-1",
        timestamp=now - timedelta(days=10),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Old visitor with video",
        media_uri=str(clip_path),
    )
    await repo.save(event)

    result = await worker.purge_expired(now=now)
    assert not clip_path.exists()
    assert result.files_deleted == 1
    assert result.bytes_freed == 1024


@pytest.mark.asyncio
async def test_retention_logs_audit_record(tmp_path: Path) -> None:
    """TR-20: Verify retention worker logs structured audit record upon purge."""
    db_file = tmp_path / "retention_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    worker = MediaRetentionWorker(
        repository=repo, routine_retention_days=7, critical_retention_days=30
    )
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    event = CameraEvent(
        id="event-audit-purge",
        camera_id="cam-1",
        timestamp=now - timedelta(days=14),
        category=EventCategory.FALSE_POSITIVE,
        threat_level=ThreatLevel.INFO,
        summary="Wind shake",
    )
    await repo.save(event)

    await worker.purge_expired(now=now)
    audit_entries = await worker.audit_logger.query(action="retention_purge")

    assert len(audit_entries) >= 1
    assert audit_entries[0]["action"] == "retention_purge"
    assert audit_entries[0]["details"]["events_purged"] == 1
