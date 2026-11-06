"""Unit tests for SQLite Event Repository."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)


@pytest.mark.asyncio
async def test_sqlite_repository_save_and_retrieve(tmp_path: Path) -> None:
    """TR-5: Verify SqliteEventRepository saves and retrieves CameraEvent by ID."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    event = CameraEvent(
        id="event-1",
        camera_id="cam-front",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident Alice arrived",
        resident_id="res-alice",
        media_uri="/data/clips/res.mp4",
        snapshot_uri="/data/snaps/res.jpg",
        metadata={"location": "front_door"},
    )

    await repo.save(event)
    retrieved = await repo.get_by_id(event.id)
    assert retrieved is not None
    assert retrieved.id == event.id
    assert retrieved.camera_id == "cam-front"
    assert retrieved.category == EventCategory.VERIFIED_RESIDENT
    assert retrieved.threat_level == ThreatLevel.INFO
    assert retrieved.resident_id == "res-alice"
    assert retrieved.metadata["location"] == "front_door"

    # Missing ID returns None
    assert await repo.get_by_id("missing") is None


@pytest.mark.asyncio
async def test_sqlite_repository_find_with_camera_and_category_filters(tmp_path: Path) -> None:
    """TR-6: Verify find() filtering by camera_id, category, and threat_level."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    e1 = CameraEvent(
        id="e1",
        camera_id="cam-1",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier drop-off",
    )
    e2 = CameraEvent(
        id="e2",
        camera_id="cam-1",
        timestamp=now - timedelta(minutes=5),
        category=EventCategory.SECURITY_INTRUSION,
        threat_level=ThreatLevel.HIGH,
        summary="Intruder detected",
    )
    e3 = CameraEvent(
        id="e3",
        camera_id="cam-2",
        timestamp=now - timedelta(minutes=10),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier at side gate",
    )

    await repo.save(e1)
    await repo.save(e2)
    await repo.save(e3)

    results = await repo.find(camera_id="cam-1", category=EventCategory.COURIER_DELIVERY)
    assert len(results) == 1
    assert results[0].id == "e1"

    threats = await repo.find(min_threat=ThreatLevel.HIGH)
    assert len(threats) == 1
    assert threats[0].id == "e2"


@pytest.mark.asyncio
async def test_sqlite_repository_find_date_range(tmp_path: Path) -> None:
    """TR-7: Verify finding events within a date range."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    base_time = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    e_past = CameraEvent(
        id="e-past",
        camera_id="cam-1",
        timestamp=base_time - timedelta(hours=2),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Old visitor",
    )
    e_mid1 = CameraEvent(
        id="e-mid1",
        camera_id="cam-1",
        timestamp=base_time,
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Mid visitor 1",
    )
    e_mid2 = CameraEvent(
        id="e-mid2",
        camera_id="cam-1",
        timestamp=base_time + timedelta(minutes=30),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Mid visitor 2",
    )
    e_future = CameraEvent(
        id="e-future",
        camera_id="cam-1",
        timestamp=base_time + timedelta(hours=2),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="Future visitor",
    )

    await repo.save(e_past)
    await repo.save(e_mid1)
    await repo.save(e_mid2)
    await repo.save(e_future)

    events = await repo.find(
        start_time=base_time - timedelta(minutes=5),
        end_time=base_time + timedelta(minutes=45),
    )
    assert len(events) == 2
    event_ids = {e.id for e in events}
    assert event_ids == {"e-mid1", "e-mid2"}

    # Also test get_timeline
    timeline = await repo.get_timeline(
        start_time=base_time - timedelta(minutes=5),
        end_time=base_time + timedelta(minutes=45),
    )
    assert len(timeline) == 2


@pytest.mark.asyncio
async def test_sqlite_repository_pagination(tmp_path: Path) -> None:
    """TR-8: Verify pagination with limit and offset."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    base_time = datetime(2026, 11, 6, 10, 0, 0, tzinfo=UTC)

    for i in range(5):
        await repo.save(
            CameraEvent(
                id=f"evt-{i}",
                camera_id="cam-1",
                timestamp=base_time + timedelta(minutes=i),
                category=EventCategory.ANIMAL_WILDLIFE,
                threat_level=ThreatLevel.INFO,
                summary=f"Wildlife {i}",
            )
        )

    # Offset 1, Limit 2
    page = await repo.find(limit=2, offset=1)
    assert len(page) == 2
    # Events sorted newest first: evt-4, evt-3, evt-2, evt-1, evt-0
    assert page[0].id == "evt-3"
    assert page[1].id == "evt-2"


@pytest.mark.asyncio
async def test_sqlite_repository_count_and_delete(tmp_path: Path) -> None:
    """TR-9: Verify count() and delete() operations."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    event = CameraEvent(
        id="to-delete",
        camera_id="cam-1",
        timestamp=now,
        category=EventCategory.FALSE_POSITIVE,
        threat_level=ThreatLevel.INFO,
        summary="Wind moved branch",
    )

    await repo.save(event)
    assert await repo.count() == 1

    assert await repo.delete(event.id) is True
    assert await repo.count() == 0
    assert await repo.get_by_id(event.id) is None

    # Deleting nonexistent returns False
    assert await repo.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_sqlite_repository_json_roundtrip_metrics_and_provenance(tmp_path: Path) -> None:
    """TR-10: Verify JSON roundtrip of DetectionMetrics, BoundingBox, and InferenceProvenance."""
    db_file = tmp_path / "test.db"
    repo = SqliteEventRepository(db_path=db_file)
    now = datetime.now(UTC)

    box = BoundingBox(
        label="person",
        confidence=0.94,
        x_min=0.1,
        y_min=0.2,
        x_max=0.5,
        y_max=0.8,
    )
    metrics = DetectionMetrics(
        confidence=0.94,
        bounding_boxes=[box],
        audio_decibels=42.5,
        duration_seconds=10.0,
        total_latency_ms=120.0,
    )
    provenance = [
        InferenceProvenance(
            provider="local_yolo",
            model_name="yolov8n",
            latency_ms=45.0,
            confidence=0.94,
            tokens_used=None,
            raw_output="detected: person",
            timestamp=now,
        )
    ]

    event = CameraEvent(
        id="event-rich",
        camera_id="cam-porch",
        timestamp=now,
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.MEDIUM,
        summary="Unknown visitor detected at door",
        metrics=metrics,
        provenance=provenance,
    )

    await repo.save(event)
    retrieved = await repo.get_by_id(event.id)

    assert retrieved is not None
    assert len(retrieved.metrics.bounding_boxes) == 1
    assert retrieved.metrics.bounding_boxes[0].label == "person"
    assert retrieved.metrics.bounding_boxes[0].confidence == 0.94
    assert retrieved.metrics.bounding_boxes[0].x_min == 0.1
    assert retrieved.metrics.audio_decibels == 42.5
    assert len(retrieved.provenance) == 1
    assert retrieved.provenance[0].provider == "local_yolo"
    assert retrieved.provenance[0].model_name == "yolov8n"
    assert retrieved.provenance[0].confidence == 0.94
