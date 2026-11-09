from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lookout.adapters.ffmpeg import FFmpegTranscoderAdapter
from lookout.config.schema import TranscodeConfig


def make_mock_proc(code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = code
    proc.communicate.return_value = (stdout, stderr)
    return proc


def test_ffmpeg_hardware_acceleration_detection() -> None:
    adapter = FFmpegTranscoderAdapter(config=TranscodeConfig(hwaccel="auto"))
    detected = adapter.detect_hardware_acceleration()
    assert detected in {"vaapi", "qsv", "videotoolbox", "nvenc", "v4l2m2m", "none"}


@pytest.mark.asyncio
async def test_ffmpeg_hardware_transcode_vaapi_command(tmp_path: Path) -> None:
    adapter = FFmpegTranscoderAdapter(
        config=TranscodeConfig(
            hwaccel="vaapi",
            hwaccel_device="/dev/dri/renderD128",
            use_stream_copy=False,
        )
    )
    with patch("asyncio.create_subprocess_exec", return_value=make_mock_proc(0, b"", b"")):
        await adapter.normalize_mp4("input.mp4", str(tmp_path / "output.mp4"))
        assert "-c:v h264_vaapi" in " ".join(adapter.last_command)


@pytest.mark.asyncio
async def test_ffmpeg_hardware_fallback_to_software_libx264(tmp_path: Path) -> None:
    fail_proc = make_mock_proc(1, b"", b"Hardware init failure")
    success_proc = make_mock_proc(0, b"", b"Software transcode complete")

    adapter = FFmpegTranscoderAdapter(
        config=TranscodeConfig(hwaccel="vaapi", use_stream_copy=False)
    )
    with patch("asyncio.create_subprocess_exec", side_effect=[fail_proc, success_proc]):
        await adapter.normalize_mp4("input.mp4", str(tmp_path / "output.mp4"))
        assert "-c:v libx264" in " ".join(adapter.last_command) and "-preset ultrafast" in " ".join(
            adapter.last_command
        )
