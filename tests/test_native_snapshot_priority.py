"""Tests for native camera snapshot priority (live frame over stale event snapshot)."""

from pathlib import Path

from fastapi.testclient import TestClient

from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig

LIVE_JPEG = b"\xff\xd8\xff\xe0" + b"live-native-frame" * 64
STALE_JPEG = b"\xff\xd8\xff\xe0" + b"stale-event-snapshot" * 64


class FakeStreamManager:
    def __init__(self, frame_bytes: bytes = LIVE_JPEG) -> None:
        self.frame_bytes = frame_bytes
        self.streams: dict[str, str] = {}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def frame(self, camera_id: str) -> bytes:
        return self.frame_bytes


def test_native_camera_snapshot_prefers_live_frame(tmp_path: Path) -> None:
    base_path = tmp_path / "data"
    base_path.mkdir(parents=True, exist_ok=True)
    # Stale event snapshot on disk.
    event_dir = base_path / "snapshots" / "events"
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "native_cam_123.jpg").write_bytes(STALE_JPEG)

    config = LookoutConfig(
        storage=StorageConfig(base_path=str(base_path)),
        cameras=[
            CameraConfig(
                id="native_cam",
                name="Native Cam",
                stream_url="wyze://192.168.1.50?uid=x&enr=y&mac=Z&model=M&subtype=hd&dtls=true",
                native_provider="wyze",
                native_device_id="Z",
            )
        ],
    )
    app = create_app(config=config)
    manager = FakeStreamManager(frame_bytes=LIVE_JPEG)
    manager.streams["native_cam"] = "wyze://192.168.1.50?uid=x"
    app.state.native_stream_manager = manager

    client = TestClient(app)
    response = client.get("/api/cameras/native_cam/snapshot")

    assert response.status_code == 200
    assert response.content == LIVE_JPEG


def test_snapshot_serves_newest_mtime_across_events_and_camera_snapshots(
    tmp_path: Path,
) -> None:
    """TR-8: Verify get_camera_snapshot selects newest st_mtime across camera & event snapshots."""
    import os

    base_path = tmp_path / "data"
    snap_dir = base_path / "snapshots"
    event_dir = snap_dir / "events"
    event_dir.mkdir(parents=True, exist_ok=True)

    older_camera_snap = b"\xff\xd8\xff\xe0" + b"older-camera-snap" * 32
    older_motion_snap = b"\xff\xd8\xff\xe0" + b"older-motion-snap" * 32
    newest_ding_snap = b"\xff\xd8\xff\xe0" + b"newest-ding-snap" * 32

    cam_file = snap_dir / "front_door.jpg"
    cam_file.write_bytes(older_camera_snap)
    os.utime(cam_file, (1000.0, 1000.0))

    # Lexicographically "front_door_motion_..." > "front_door_ding_...",
    # but ding has a newer mtime and must win.
    motion_file = event_dir / "front_door_motion_20260101_120000.jpg"
    motion_file.write_bytes(older_motion_snap)
    os.utime(motion_file, (2000.0, 2000.0))

    ding_file = event_dir / "front_door_ding_20260102_080000.jpg"
    ding_file.write_bytes(newest_ding_snap)
    os.utime(ding_file, (3000.0, 3000.0))

    config = LookoutConfig(
        storage=StorageConfig(base_path=str(base_path)),
        cameras=[
            CameraConfig(
                id="front_door",
                name="Front Door",
                stream_url="rtsp://127.0.0.1/front_door",
                camera_type="ring",
                stream_mode="on_demand",
            )
        ],
    )
    app = create_app(config=config)
    client = TestClient(app)

    res = client.get("/api/cameras/front_door/snapshot")
    assert res.status_code == 200
    assert res.content == newest_ding_snap


def test_manual_snapshot_updates_top_level_and_serves_immediately(
    tmp_path: Path,
) -> None:
    """TR-9: Verify manual POST /api/cameras/{id}/snapshot updates camera snapshot and serves it."""
    import os

    base_path = tmp_path / "data"
    event_dir = base_path / "snapshots" / "events"
    event_dir.mkdir(parents=True, exist_ok=True)

    stale_snap = b"\xff\xd8\xff\xe0" + b"stale-event" * 32
    stale_file = event_dir / "front_door_motion_20260101_000000.jpg"
    stale_file.write_bytes(stale_snap)
    os.utime(stale_file, (1000.0, 1000.0))

    fresh_snap = b"\xff\xd8\xff\xe0" + b"fresh-manual-capture" * 32
    scratch_file = tmp_path / "manual_capture.jpg"
    scratch_file.write_bytes(fresh_snap)

    config = LookoutConfig(
        storage=StorageConfig(base_path=str(base_path)),
        cameras=[
            CameraConfig(
                id="front_door",
                name="Front Door",
                stream_url="rtsp://127.0.0.1/front_door",
                camera_type="ring",
                stream_mode="on_demand",
            )
        ],
    )
    app = create_app(config=config)
    app.state.api_key = "test-secret"

    async def fake_capture(camera_id: str) -> str:
        return str(scratch_file)

    app.state.ptz_service.capture_snapshot = fake_capture

    client = TestClient(app)
    post_res = client.post(
        "/api/cameras/front_door/snapshot",
        headers={"X-API-Key": "test-secret"},
    )
    assert post_res.status_code == 200

    top_level = base_path / "snapshots" / "front_door.jpg"
    assert top_level.is_file()
    assert top_level.read_bytes() == fresh_snap

    get_res = client.get("/api/cameras/front_door/snapshot")
    assert get_res.status_code == 200
    assert get_res.content == fresh_snap
