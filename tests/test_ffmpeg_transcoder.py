"""Unit tests for FFmpeg transcoder adapter and mock transcoder."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lookout.adapters.ffmpeg import (
    FFmpegNotFoundError,
    FFmpegTranscoderAdapter,
    TranscodeError,
)
from lookout.adapters.memory.transcode import MockTranscoderAdapter
from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import TranscodeConfig
from lookout.ports.egress import MediaTranscoderPort


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    """Helper creating a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


@pytest.mark.asyncio
async def test_mock_transcoder_adapter() -> None:
    """TR-15: Verify MockTranscoderAdapter implements MediaTranscoderPort protocol."""
    assert isinstance(MockTranscoderAdapter(), MediaTranscoderPort)

    mock = MockTranscoderAdapter()
    norm = await mock.normalize_mp4("/input/video.mp4")
    assert norm.endswith(".mp4")
    gif = await mock.generate_preview_gif("/input/video.mp4")
    assert gif.endswith(".gif")
    webp = await mock.generate_preview_webp("/input/video.mp4")
    assert webp.endswith(".webp")
    thumbs = await mock.generate_thumbnails("/input/video.mp4", count=3)
    assert len(thumbs) == 3


@pytest.mark.asyncio
async def test_ffmpeg_stream_copy_remux_primary_path(tmp_path: Path) -> None:
    """TR-9: Verify zero-copy remux path includes '-c copy'."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(temp_manager=manager)

    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(0, b"", b"")):
        out_path = await adapter.normalize_mp4(str(tmp_path / "in.mp4"))
        command_str = " ".join(adapter.last_command)
        assert "-c copy" in command_str
        assert out_path.endswith(".mp4")


@pytest.mark.asyncio
async def test_ffmpeg_transcode_fallback_on_stream_copy_failure(tmp_path: Path) -> None:
    """TR-10: Verify stream copy failure triggers fallback transcode with '-preset ultrafast'."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(temp_manager=manager)

    # First call (stream-copy) fails; second call (fallback transcode) succeeds
    mock_fail = _mock_proc(1, b"", b"Non-monotonic DTS in input stream")
    mock_success = _mock_proc(0, b"", b"Transcode complete")
    with patch("asyncio.create_subprocess_exec", side_effect=[mock_fail, mock_success]):
        out_path = await adapter.normalize_mp4(str(tmp_path / "in.mp4"))
        command_str = " ".join(adapter.last_command)
        assert "-preset ultrafast" in command_str
        assert out_path.endswith(".mp4")


@pytest.mark.asyncio
async def test_ffmpeg_generate_fast_preview_gif(tmp_path: Path) -> None:
    """TR-11: Verify 128-color bayer-dithered palette in preview GIF pipeline."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(temp_manager=manager)

    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(0, b"", b"")):
        gif_path = await adapter.generate_preview_gif(str(tmp_path / "in.mp4"))
        command_str = " ".join(adapter.last_command)
        assert "palettegen=max_colors=128" in command_str
        assert gif_path.endswith(".gif")


@pytest.mark.asyncio
async def test_ffmpeg_generate_preview_webp(tmp_path: Path) -> None:
    """TR-12: Verify animated WebP generation returns .webp path."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(temp_manager=manager)

    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(0, b"", b"")):
        webp_path = await adapter.generate_preview_webp(str(tmp_path / "in.mp4"))
        assert webp_path.endswith(".webp")


@pytest.mark.asyncio
async def test_ffmpeg_single_pass_thumbnails(tmp_path: Path) -> None:
    """TR-13: Verify single-pass thumbnail extraction extracts count stills."""
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(temp_manager=manager)

    # ffprobe duration check returns 10.0, extraction returns 0
    probe_proc = _mock_proc(0, b"10.0\n", b"")
    extract_proc = _mock_proc(0, b"", b"")
    with patch("asyncio.create_subprocess_exec", side_effect=[probe_proc, extract_proc]):
        thumbnails = await adapter.generate_thumbnails(
            str(tmp_path / "in.mp4"),
            output_dir=str(tmp_path / "thumbs"),
            count=5,
        )
        assert len(thumbnails) == 5


@pytest.mark.asyncio
async def test_ffmpeg_subprocess_niceness_and_thread_limits(tmp_path: Path) -> None:
    """TR-14: Verify command includes thread cap parameter '-threads 2'."""
    config = TranscodeConfig(max_threads=2, nice_level=10)
    manager = TempMediaManager(preferred_temp_dir=str(tmp_path))
    adapter = FFmpegTranscoderAdapter(config=config, temp_manager=manager)

    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(0, b"", b"")):
        await adapter.generate_preview_gif(str(tmp_path / "in.mp4"))
        command_str = " ".join(adapter.last_command)
        assert "-threads 2" in command_str


@pytest.mark.asyncio
async def test_ffmpeg_not_found_raises() -> None:
    """Verify missing FFmpeg executable raises FFmpegNotFoundError."""
    config = TranscodeConfig(ffmpeg_path="/nonexistent/ffmpeg")
    adapter = FFmpegTranscoderAdapter(config=config)
    with pytest.raises(FFmpegNotFoundError):
        await adapter._run_command(["/nonexistent/ffmpeg", "-version"])


@pytest.mark.asyncio
async def test_transcode_failure_raises() -> None:
    """Verify unrecoverable transcode error raises TranscodeError."""
    adapter = FFmpegTranscoderAdapter(config=TranscodeConfig(use_stream_copy=False))
    err_proc = _mock_proc(1, b"", b"Severe codec error")
    with (
        patch("asyncio.create_subprocess_exec", return_value=err_proc),
        pytest.raises(TranscodeError),
    ):
        await adapter.normalize_mp4("/input/corrupt.mp4")
