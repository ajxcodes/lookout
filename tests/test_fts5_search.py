"""Full-Text Search (FTS5) unit tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@pytest.mark.asyncio
async def test_fts5_search_summary_keyword(tmp_path: Path) -> None:
    """TR-13: Verify FTS5 search finds events by summary keyword."""
    db_file = tmp_path / "fts_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    e1 = CameraEvent(
        id="event-courier",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier arrived with overnight priority delivery",
    )
    e2 = CameraEvent(
        id="event-cat",
        camera_id="cam-backyard",
        timestamp=now,
        category=EventCategory.ANIMAL_WILDLIFE,
        threat_level=ThreatLevel.INFO,
        summary="Raccoon rummaging near compost bin",
    )

    await repo.save(e1)
    await repo.save(e2)

    results = await repo.find(query="courier")
    assert len(results) == 1
    assert results[0].id == "event-courier"


@pytest.mark.asyncio
async def test_fts5_search_tags(tmp_path: Path) -> None:
    """TR-14: Verify FTS5 search indexes metadata tags."""
    db_file = tmp_path / "fts_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    e1 = CameraEvent(
        id="event-delivery",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.VEHICLE,
        threat_level=ThreatLevel.LOW,
        summary="Unmarked vehicle idling in front driveway",
        metadata={"tags": ["delivery", "van", "courier_van"]},
    )
    e2 = CameraEvent(
        id="event-lawn",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Lawn care worker with mower",
        metadata={"tags": ["maintenance"]},
    )

    await repo.save(e1)
    await repo.save(e2)

    results = await repo.find(query="delivery")
    assert len(results) == 1
    assert results[0].id == "event-delivery"


@pytest.mark.asyncio
async def test_fts5_triggers_sync_on_insert_update_and_delete(tmp_path: Path) -> None:
    """TR-15: Verify database triggers synchronize FTS index on insert, update, and delete."""
    db_file = tmp_path / "fts_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    event = CameraEvent(
        id="event-lifecycle",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.PACKAGE_EVENT,
        threat_level=ThreatLevel.LOW,
        summary="Amazon package box placed at porch threshold",
    )

    # Insert sync
    await repo.save(event)
    res_insert = await repo.find(query="threshold")
    assert len(res_insert) == 1
    assert res_insert[0].id == "event-lifecycle"

    # Update sync
    event.summary = "Letter envelope slipped under door"
    await repo.save(event)
    res_old = await repo.find(query="threshold")
    assert len(res_old) == 0
    res_updated = await repo.find(query="envelope")
    assert len(res_updated) == 1
    assert res_updated[0].id == "event-lifecycle"

    # Delete sync
    await repo.delete("event-lifecycle")
    results_after_delete = await repo.find(query="envelope")
    assert len(results_after_delete) == 0


@pytest.mark.asyncio
async def test_fts5_query_sanitization_with_punctuation(tmp_path: Path) -> None:
    """TR-16: Verify query sanitization handles punctuation, quotes, and boolean terms safely."""
    db_file = tmp_path / "fts_test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    event = CameraEvent(
        id="event-punct",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier delivered parcel to front door",
    )
    await repo.save(event)

    # Queries containing punctuation and brackets that would break raw FTS5 syntax
    weird_query = 'courier, parcel! (delivered?) * "front" OR NOT'
    results = await repo.find(query=weird_query)
    assert len(results) == 1
    assert results[0].id == "event-punct"

    # Blank query returns empty list
    empty_results = await repo.find(query="   !@#$%^&*()   ")
    assert len(empty_results) == 0
