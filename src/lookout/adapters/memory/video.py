"""Mock video recorder adapter generating synthetic file paths."""

from lookout.ports.egress import VideoRecorderPort


class MockVideoRecorderAdapter(VideoRecorderPort):
    """Synthetic video recorder adapter returning mock storage URIs."""

    def __init__(self, base_storage_uri: str = "/mock/media") -> None:
        self.base_storage_uri = base_storage_uri

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        """Return synthetic video clip storage URI."""
        return f"{self.base_storage_uri}/recordings/{camera_id}_{duration_seconds:.1f}s.mp4"

    async def capture_snapshot(self, camera_id: str) -> str:
        """Return synthetic snapshot image storage URI."""
        return f"{self.base_storage_uri}/snapshots/{camera_id}.jpg"
