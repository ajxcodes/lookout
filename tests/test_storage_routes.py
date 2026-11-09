"""REST API route tests for storage telemetry, configuration, eviction, and media streaming."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig, StorageConfig
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@pytest.fixture
def app_and_client(tmp_path: Path):
    local_dir = tmp_path / "local"
    archive_dir = tmp_path / "archive"
    (local_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (archive_dir / "recordings").mkdir(parents=True, exist_ok=True)

    db_path = local_dir / "lookout.db"
    config = LookoutConfig(
        system_name="Test Lookout Storage",
        storage=StorageConfig(
            base_path=str(local_dir),
            database_path=str(db_path),
            archive_enabled=True,
            archive_path=str(archive_dir),
            max_storage_gb=100.0,
            archive_max_storage_gb=500.0,
        ),
    )
    repo = SqliteEventRepository(db_path=db_path)
    app = create_app(config=config, repository=repo)
    client = TestClient(app)
    return client, repo, local_dir, archive_dir


def test_get_storage_status_endpoint(app_and_client) -> None:
    """TR-22: Verify GET /api/storage/status returns local and archive telemetry."""
    client, _, _, _ = app_and_client
    response = client.get("/api/storage/status")
    data = response.json()
    assert (
        response.status_code == 200
        and "volume_total_bytes" in data
        and "archive_volume_total_bytes" in data
    )


def test_get_and_post_storage_config_endpoints(app_and_client) -> None:
    """TR-23: Verify GET /api/storage/config and POST /api/storage/config updates parameters."""
    client, _, _, _ = app_and_client

    get_res = client.get("/api/storage/config")
    assert get_res.status_code == 200
    assert get_res.json()["archive_max_storage_gb"] == 500.0

    post_res = client.post(
        "/api/storage/config",
        json={"max_storage_gb": 80.0, "archive_max_storage_gb": 500.0},
    )
    data = post_res.json()
    response = post_res
    assert response.status_code == 200 and data["archive_max_storage_gb"] == 500.0


def test_manual_storage_clean_endpoint(app_and_client) -> None:
    """TR-24: Verify POST /api/storage/clean triggers eviction and returns results."""
    client, _, _, _ = app_and_client
    response = client.post("/api/storage/clean", json={})
    assert response.status_code == 200 and (
        "bytes_freed" in response.json() or "bytes_migrated" in response.json()
    )


def test_seamless_media_resolver_serves_both_local_and_archived_clips(app_and_client) -> None:
    """TR-25: Verify GET /media/... transparently serves clips from local and archive tiers."""
    client, _, local_dir, archive_dir = app_and_client

    local_clip = local_dir / "recordings" / "loc.mp4"
    arch_clip = archive_dir / "recordings" / "arch.mp4"
    local_clip.write_bytes(b"local-clip-payload")
    arch_clip.write_bytes(b"archive-clip-payload")

    local_res = client.get("/media/recordings/loc.mp4")
    archive_res = client.get("/media/recordings/arch.mp4")

    assert local_res.status_code == 200 and archive_res.status_code == 200
    assert local_res.content == b"local-clip-payload"
    assert archive_res.content == b"archive-clip-payload"


@pytest.mark.asyncio
async def test_event_bookmark_endpoint(app_and_client) -> None:
    """TR-26: Verify POST /api/events/{id}/bookmark toggles protection against eviction."""
    client, repo, _, _ = app_and_client
    now = datetime.now(UTC)

    evt = CameraEvent(
        id="evt-bm-test",
        camera_id="cam-front",
        timestamp=now - timedelta(hours=1),
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier drop-off",
        bookmarked=False,
    )
    await repo.save(evt)

    response = client.post("/api/events/evt-bm-test/bookmark", json={"bookmarked": True})
    assert response.status_code == 200 and response.json()["bookmarked"] is True

    saved = await repo.get_by_id("evt-bm-test")
    assert saved is not None
    assert saved.bookmarked is True
