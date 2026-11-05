"""Mock face recognition adapter for deterministic face identification."""

from lookout.ports.egress import FaceRecognitionPort


class MockFaceRecognitionAdapter(FaceRecognitionPort):
    """Deterministic face recognition adapter for testing."""

    def __init__(
        self,
        recognized_faces: list[tuple[str, float]] | None = None,
    ) -> None:
        self.recognized_faces = (
            recognized_faces if recognized_faces is not None else [("resident_alice", 0.95)]
        )

    async def identify_faces(self, image_bytes: bytes) -> list[tuple[str, float]]:
        """Return configured recognized faces."""
        return list(self.recognized_faces)
