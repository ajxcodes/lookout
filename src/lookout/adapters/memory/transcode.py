"""In-memory mock transcoder adapter for testing and offline development."""

from pathlib import Path

from lookout.ports.egress import MediaTranscoderPort


class MockTranscoderAdapter(MediaTranscoderPort):
    """Synthetic transcoder adapter providing mock file paths and tracking calls."""

    def __init__(self, base_storage_uri: str = "/mock/transcode") -> None:
        self.base_storage_uri = base_storage_uri
        self.normalized_calls: list[tuple[str, str | None]] = []
        self.gif_calls: list[tuple[str, str | None, float, int, int]] = []
        self.webp_calls: list[tuple[str, str | None, float, int, int]] = []
        self.thumbnail_calls: list[tuple[str, str | None, int, int]] = []

    async def normalize_mp4(self, input_path: str, output_path: str | None = None) -> str:
        """Return normalized MP4 mock path."""
        self.normalized_calls.append((input_path, output_path))
        if output_path is not None:
            return output_path
        stem = Path(input_path).stem
        return f"{self.base_storage_uri}/{stem}_normalized.mp4"

    async def generate_preview_gif(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Return preview GIF mock path."""
        self.gif_calls.append((input_path, output_path, duration_seconds, fps, width))
        if output_path is not None:
            return output_path
        stem = Path(input_path).stem
        return f"{self.base_storage_uri}/{stem}_preview.gif"

    async def generate_preview_webp(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Return preview WebP mock path."""
        self.webp_calls.append((input_path, output_path, duration_seconds, fps, width))
        if output_path is not None:
            return output_path
        stem = Path(input_path).stem
        return f"{self.base_storage_uri}/{stem}_preview.webp"

    async def generate_thumbnails(
        self,
        input_path: str,
        output_dir: str | None = None,
        count: int = 5,
        width: int = 320,
    ) -> list[str]:
        """Return synthetic thumbnail mock paths."""
        self.thumbnail_calls.append((input_path, output_dir, count, width))
        out_dir = output_dir or f"{self.base_storage_uri}/thumbs"
        stem = Path(input_path).stem
        return [f"{out_dir}/{stem}_thumb_{i:03d}.jpg" for i in range(1, count + 1)]
