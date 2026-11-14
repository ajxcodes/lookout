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
