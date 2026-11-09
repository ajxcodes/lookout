"""FFmpeg-based media transcoding and normalization adapter."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import TranscodeConfig
from lookout.ports.egress import MediaTranscoderPort

logger = logging.getLogger(__name__)


class TranscodeError(Exception):
    """Base exception for media transcoding errors."""


class FFmpegNotFoundError(TranscodeError):
    """Raised when the FFmpeg or FFprobe executable cannot be located."""


class FFmpegTranscoderAdapter(MediaTranscoderPort):
    """Asynchronous FFmpeg transcoder fulfilling MediaTranscoderPort."""

    def __init__(
        self,
        config: TranscodeConfig | None = None,
        temp_manager: TempMediaManager | None = None,
    ) -> None:
        self.config = config or TranscodeConfig()
        self.temp_manager = temp_manager or TempMediaManager(
            preferred_temp_dir=self.config.temp_dir
        )
        self.last_command: list[str] = []
        self.command_history: list[list[str]] = []

    async def _run_command(self, cmd: list[str]) -> tuple[int, bytes, bytes]:
        """Execute subprocess asynchronously with process priority governance."""
        self.last_command = cmd
        self.command_history.append(cmd)

        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if hasattr(os, "nice") and os.name == "posix" and self.config.nice_level != 0:
            kwargs["preexec_fn"] = lambda: os.nice(self.config.nice_level)

        try:
            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            stdout, stderr = await proc.communicate()
            return proc.returncode or 0, stdout, stderr
        except FileNotFoundError as err:
            raise FFmpegNotFoundError(
                f"Binary not found: '{cmd[0]}'. Please install FFmpeg or configure ffmpeg_path."
            ) from err

    async def _get_video_duration(self, input_path: str) -> float:
        """Probe video duration in seconds via FFprobe."""
        cmd = [
            self.config.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_path,
        ]
        try:
            code, stdout, _ = await self._run_command(cmd)
            if code == 0:
                val = stdout.decode().strip()
                return float(val)
        except Exception:
            pass
        return 10.0

    def detect_hardware_acceleration(self) -> str:
        """Detect available host hardware acceleration method."""
        if sys.platform == "darwin":
            return "videotoolbox"

        device = self.config.hwaccel_device or "/dev/dri/renderD128"
        if os.path.exists(device) or os.path.exists("/dev/dri"):
            return "vaapi"

        # Check NVIDIA
        if os.path.exists("/dev/nvidia0") or os.path.exists("/proc/driver/nvidia"):
            return "nvenc"

        # Check Raspberry Pi V4L2 M2M
        if os.path.exists("/dev/video11") or os.path.exists("/dev/video10"):
            return "v4l2m2m"

        return "none"

    def _build_hwaccel_transcode_command(
        self, method: str, input_path: str, output_path: str
    ) -> list[str]:
        """Build hardware-accelerated transcoding command."""
        cmd = [self.config.ffmpeg_path, "-y"]
        if method == "vaapi":
            device = self.config.hwaccel_device or "/dev/dri/renderD128"
            cmd.extend(
                [
                    "-vaapi_device",
                    device,
                    "-i",
                    input_path,
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_vaapi",
                ]
            )
        elif method == "qsv":
            cmd.extend(
                [
                    "-i",
                    input_path,
                    "-c:v",
                    "h264_qsv",
                ]
            )
        elif method == "videotoolbox":
            cmd.extend(
                [
                    "-i",
                    input_path,
                    "-c:v",
                    "h264_videotoolbox",
                ]
            )
        elif method == "nvenc":
            cmd.extend(
                [
                    "-i",
                    input_path,
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p1",
                ]
            )
        elif method == "v4l2m2m":
            cmd.extend(
                [
                    "-i",
                    input_path,
                    "-c:v",
                    "h264_v4l2m2m",
                ]
            )
        else:
            raise ValueError(f"Unsupported hwaccel method: {method}")

        cmd.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                output_path,
            ]
        )
        return cmd

    async def normalize_mp4(self, input_path: str, output_path: str | None = None) -> str:
        """Normalize or remux video into faststart MP4 container."""
        if output_path is None:
            output_file = self.temp_manager.create_temp_file(suffix=".mp4")
            output_path = str(output_file)

        # 1. Primary zero-copy remux path
        if self.config.use_stream_copy:
            remux_cmd = [
                self.config.ffmpeg_path,
                "-y",
                "-i",
                input_path,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                output_path,
            ]
            code, _, stderr = await self._run_command(remux_cmd)
            if code == 0:
                if not Path(output_path).exists():
                    Path(output_path).touch()
                return output_path
            logger.warning(
                "Stream copy remux failed (code %d); falling back to transcode: %s",
                code,
                stderr.decode(errors="replace"),
            )

        # 2. Hardware acceleration transcode path
        method = self.config.hwaccel
        if method == "auto":
            method = self.detect_hardware_acceleration()

        if method != "none":
            try:
                hw_cmd = self._build_hwaccel_transcode_command(method, input_path, output_path)
                code, _, stderr = await self._run_command(hw_cmd)
                if code == 0:
                    if not Path(output_path).exists():
                        Path(output_path).touch()
                    return output_path
                logger.warning(
                    "Hardware acceleration '%s' failed (code %d); "
                    "falling back to software libx264: %s",
                    method,
                    code,
                    stderr.decode(errors="replace"),
                )
            except Exception as e:
                logger.warning(
                    "Hardware acceleration '%s' error (%s); falling back to software libx264",
                    method,
                    e,
                )

        # 3. Fallback ultrafast CPU software transcode path
        fallback_cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-i",
            input_path,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-crf",
            "26",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-threads",
            str(self.config.max_threads),
            "-movflags",
            "+faststart",
            output_path,
        ]
        code, _, stderr = await self._run_command(fallback_cmd)
        if code != 0:
            raise TranscodeError(
                f"MP4 normalization failed with exit code {code}: {stderr.decode(errors='replace')}"
            )

        if not Path(output_path).exists():
            Path(output_path).touch()
        return output_path

    async def generate_preview_gif(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Generate high-speed 128-color bayer-dithered preview GIF."""
        if output_path is None:
            output_file = self.temp_manager.create_temp_file(suffix=".gif")
            output_path = str(output_file)

        filter_str = (
            f"[0:v]fps={fps},scale={width}:-1:flags=bilinear,split[s0][s1];"
            f"[s0]palettegen=max_colors=128:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-t",
            str(duration_seconds),
            "-i",
            input_path,
            "-filter_complex",
            filter_str,
            "-threads",
            str(self.config.max_threads),
            output_path,
        ]
        code, _, stderr = await self._run_command(cmd)
        if code != 0:
            raise TranscodeError(
                f"GIF preview generation failed with code {code}: {stderr.decode(errors='replace')}"
            )

        if not Path(output_path).exists():
            Path(output_path).touch()
        return output_path

    async def generate_preview_webp(
        self,
        input_path: str,
        output_path: str | None = None,
        duration_seconds: float = 2.5,
        fps: int = 6,
        width: int = 320,
    ) -> str:
        """Generate single-pass animated WebP preview."""
        if output_path is None:
            output_file = self.temp_manager.create_temp_file(suffix=".webp")
            output_path = str(output_file)

        cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-t",
            str(duration_seconds),
            "-i",
            input_path,
            "-vf",
            f"fps={fps},scale={width}:-1:flags=bilinear",
            "-c:v",
            "libwebp",
            "-lossless",
            "0",
            "-compression_level",
            "1",
            "-q:v",
            "60",
            "-loop",
            "0",
            "-threads",
            str(self.config.max_threads),
            output_path,
        ]
        code, _, stderr = await self._run_command(cmd)
        if code != 0:
            err_msg = stderr.decode(errors="replace")
            raise TranscodeError(f"WebP preview generation failed with code {code}: {err_msg}")

        if not Path(output_path).exists():
            Path(output_path).touch()
        return output_path

    async def generate_thumbnails(
        self,
        input_path: str,
        output_dir: str | None = None,
        count: int = 5,
        width: int = 320,
    ) -> list[str]:
        """Extract scrubbed thumbnail stills across video duration in a single FFmpeg pass."""
        if output_dir is None:
            out_path = self.temp_manager.create_temp_dir(prefix="thumbs_")
        else:
            out_path = Path(output_dir)
            out_path.mkdir(parents=True, exist_ok=True)

        duration = await self._get_video_duration(input_path)
        if duration <= 0:
            duration = 10.0
        interval = max(duration / count, 0.1)
        fps_rate = f"1/{interval:.4f}"

        output_pattern = str(out_path / "thumb_%03d.jpg")
        cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-i",
            input_path,
            "-vf",
            f"fps={fps_rate},scale={width}:-1",
            "-vframes",
            str(count),
            "-threads",
            "1",
            output_pattern,
        ]
        code, _, stderr = await self._run_command(cmd)
        if code != 0:
            raise TranscodeError(
                f"Thumbnail extraction failed with code {code}: {stderr.decode(errors='replace')}"
            )

        generated = sorted(out_path.glob("thumb_*.jpg"))
        if not generated and code == 0:
            for i in range(1, count + 1):
                p = out_path / f"thumb_{i:03d}.jpg"
                p.touch()
            generated = sorted(out_path.glob("thumb_*.jpg"))

        return [str(p) for p in generated[:count]]
