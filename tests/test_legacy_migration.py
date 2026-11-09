"""Unit tests for legacy historical event migration utility."""

import json
from pathlib import Path

import pytest

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.migration import import_legacy_events
from lookout.adapters.sqlite.repository import SqliteEventRepository


@pytest.mark.asyncio
async def test_legacy_import_clean_without_data_loss(tmp_path: Path) -> None:
    """TR-21: Verify historical import from legacy JSON imports all 3 records cleanly."""
    json_path = tmp_path / "events.json"
    data = [
        {
            "id": "legacy-1",
            "camera_id": "cam-porch",
            "timestamp": "2026-10-01T10:00:00Z",
            "category": "courier_delivery",
            "threat_level": 1,
            "summary": "Courier drop",
        },
        {
            "id": "legacy-2",
            "camera": "cam-backyard",
            "time": 1761912000,
            "type": "animal_wildlife",
            "severity": 0,
            "description": "Raccoon passing through",
        },
        {
            "id": "legacy-3",
            "camera_id": "cam-driveway",
            "created_at": "2026-10-03T18:30:00Z",
            "category": "unknown_visitor",
            "is_threat": True,
            "title": "Visitor loitering",
        },
    ]
    json_path.write_text(json.dumps(data), encoding="utf-8")

    db_path = tmp_path / "migration.db"
    repo = SqliteEventRepository(db_path=db_path)

    result = await import_legacy_events(json_path, repo)
    assert result.imported_count == 3
    assert await repo.count() == 3

    e1 = await repo.get_by_id("legacy-1")
    assert e1 is not None
    assert e1.camera_id == "cam-porch"

    e2 = await repo.get_by_id("legacy-2")
    assert e2 is not None
    assert e2.summary == "Raccoon passing through"


@pytest.mark.asyncio
async def test_legacy_import_preserves_unmapped_fields_in_metadata(tmp_path: Path) -> None:
    """TR-22: Verify all unmapped legacy fields are preserved in metadata['legacy']."""
    json_path = tmp_path / "unmapped.json"
    data = [
        {
            "id": "legacy-custom",
            "camera_id": "cam-1",
            "timestamp": "2026-10-05T12:00:00Z",
            "category": "unknown_visitor",
            "summary": "Visitor",
            "legacy_custom_key": "custom_value",
            "legacy_sensor_temp": 72.5,
            "legacy_flag": True,
        }
    ]
    json_path.write_text(json.dumps(data), encoding="utf-8")

    db_path = tmp_path / "unmapped.db"
    repo = SqliteEventRepository(db_path=db_path)

    await import_legacy_events(json_path, repo)
    retrieved = await repo.get_by_id("legacy-custom")

    assert retrieved is not None
    assert retrieved.metadata["legacy"]["legacy_custom_key"] == "custom_value"
    assert retrieved.metadata["legacy"]["legacy_sensor_temp"] == 72.5
    assert retrieved.metadata["legacy"]["legacy_flag"] is True


@pytest.mark.asyncio
async def test_legacy_import_supports_multiple_json_formats(tmp_path: Path) -> None:
    """TR-23: Verify import supports array, nested, and keyed dict formats without errors."""
    db_path = tmp_path / "formats.db"
    repo = SqliteEventRepository(db_path=db_path)

    # 1. Nested {"events": [...]}
    nested_path = tmp_path / "nested.json"
    nested_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "nested-1",
                        "camera_id": "c1",
                        "timestamp": "2026-10-06T12:00:00Z",
                        "category": "package_event",
                        "summary": "Box",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = await import_legacy_events(nested_path, repo)
    assert result.errors == []
    assert result.imported_count == 1

    # 2. Keyed dictionary {"evt-key-1": {...}}
    dict_path = tmp_path / "keyed.json"
    dict_path.write_text(
        json.dumps(
            {
                "keyed-1": {
                    "camera_id": "c1",
                    "timestamp": "2026-10-06T13:00:00Z",
                    "category": "vehicle",
                    "summary": "Truck",
                }
            }
        ),
        encoding="utf-8",
    )
    result_dict = await import_legacy_events(dict_path, repo)
    assert result_dict.errors == []
    assert result_dict.imported_count == 1
    assert await repo.get_by_id("keyed-1") is not None


@pytest.mark.asyncio
async def test_legacy_import_creates_audit_entry(tmp_path: Path) -> None:
    """TR-24: Verify migration creates a historical_import audit log entry."""
    json_path = tmp_path / "audit_test.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "id": "audit-test-1",
                    "camera_id": "c1",
                    "timestamp": "2026-10-07T12:00:00Z",
                    "category": "vehicle",
                    "summary": "Car",
                }
            ]
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "audit_mig.db"
    repo = SqliteEventRepository(db_path=db_path)
    logger = AuditLogger(connection_manager=repo.manager)

    await import_legacy_events(json_path, repo, audit_logger=logger)
    entries = await logger.query(action="historical_import")

    assert len(entries) >= 1
    audit_entry = entries[0]
    assert audit_entry["action"] == "historical_import"
    assert audit_entry["details"]["imported_count"] == 1


@pytest.mark.asyncio
async def test_legacy_import_media_and_snapshot_url_mapping_and_normalization(
    tmp_path: Path,
) -> None:
    """Verify legacy migration maps clip_url, snapshot_url, gif_url and normalizes /local paths."""
    from lookout.adapters.sqlite.migration import KNOWN_MAPPED_FIELDS

    assert "clip_url" in KNOWN_MAPPED_FIELDS
    assert "snapshot_url" in KNOWN_MAPPED_FIELDS
    assert "gif_url" in KNOWN_MAPPED_FIELDS

    json_path = tmp_path / "urls_legacy.json"
    data = [
        {
            "id": "legacy-url-1",
            "camera_id": "cam-driveway",
            "timestamp": "2026-10-08T10:00:00Z",
            "category": "vehicle",
            "summary": "Vehicle detected",
            "clip_url": "/local/snapshots/events/car_pass.mp4",
            "snapshot_url": "/local/snapshots/events/car_pass.jpg",
        },
        {
            "id": "legacy-url-2",
            "camera_id": "cam-backyard",
            "timestamp": "2026-10-08T11:00:00Z",
            "category": "pet",
            "summary": "Dog playing",
            "video_url": "https://storage.example.com/recordings/dog.mp4",
            "gif_url": "https://storage.example.com/recordings/dog.gif",
        },
    ]
    json_path.write_text(json.dumps(data), encoding="utf-8")

    db_path = tmp_path / "urls_mig.db"
    repo = SqliteEventRepository(db_path=db_path)

    result = await import_legacy_events(json_path, repo)
    assert result.imported_count == 2
    assert result.errors == []

    e1 = await repo.get_by_id("legacy-url-1")
    assert e1 is not None
    assert e1.media_uri == "/media/events/car_pass.mp4"
    assert e1.snapshot_uri == "/media/events/car_pass.jpg"
    assert "clip_url" not in e1.metadata.get("legacy", {})
    assert "snapshot_url" not in e1.metadata.get("legacy", {})

    e2 = await repo.get_by_id("legacy-url-2")
    assert e2 is not None
    assert e2.media_uri == "https://storage.example.com/recordings/dog.mp4"
    assert e2.snapshot_uri == "https://storage.example.com/recordings/dog.gif"
    assert "video_url" not in e2.metadata.get("legacy", {})
    assert "gif_url" not in e2.metadata.get("legacy", {})
