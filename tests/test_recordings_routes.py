"""Tests for continuous recordings REST routes."""

from pathlib import Path

from fastapi.testclient import TestClient

from lookout.adapters.continuous_recorder import ContinuousRecorderService
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig


def _make_app(tmp_path: Path):
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    cfg = LookoutConfig(storage=StorageConfig(base_path=str(media)))
    # add a camera
    cfg.cameras = [CameraConfig(id="cam1", name="cam1", stream_url="rtsp://x")]
    app = create_app(config=cfg, repository=InMemoryEventRepository())
    # continuous recorder
    recorder = ContinuousRecorderService(config=cfg, cameras=cfg.cameras, storage_base_path=media)
    app.state.continuous_recorder = recorder
    return app, media


def test_list_continuous_segments_by_camera_date(tmp_path: Path) -> None:
    app, media = _make_app(tmp_path)
    seg = media / "continuous" / "cam1" / "2026" / "11" / "19" / "12-00-00.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x" * 100)
    client = TestClient(app)
    response = client.get(
        "/api/recordings/segments", params={"camera_id": "cam1", "date": "2026-11-19"}
    )
    assert response.status_code == 200 and isinstance(response.json()["segments"], list)


def test_attach_segment_to_event_copies_file(tmp_path: Path) -> None:
    from datetime import datetime

    from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel

    app, media = _make_app(tmp_path)
    seg = media / "continuous" / "cam1" / "2026" / "11" / "19" / "12-00-00.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x" * 100)

    repo = app.state.repository
    event = CameraEvent(
        id="evt1",
        camera_id="cam1",
        timestamp=datetime.utcnow(),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.LOW,
        summary="test",
    )
    import asyncio as _a

    _a.run(repo.save(event))

    client = TestClient(app)
    response = client.post(
        "/api/recordings/attach",
        json={"segment_path": str(seg), "event_id": "evt1"},
    )
    assert response.status_code == 200
    updated = _a.run(repo.get_by_id("evt1"))
    segment_path = str(seg)
    assert (
        updated.media_uri != segment_path
        and Path(updated.media_uri).exists()
        and Path(segment_path).exists()
    )


def test_recorder_status_endpoint(tmp_path: Path) -> None:
    app, media = _make_app(tmp_path)
    client = TestClient(app)
    response = client.get("/api/recordings/status")
    assert response.status_code == 200 and "cam1" in response.json()
