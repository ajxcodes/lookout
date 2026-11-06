"""Lookout adapters layer."""

from lookout.adapters.ffmpeg import FFmpegNotFoundError, FFmpegTranscoderAdapter, TranscodeError
from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.ring import RingAdapter
from lookout.adapters.tempfile import TempMediaContext, TempMediaManager

__all__ = [
    "FFmpegNotFoundError",
    "FFmpegTranscoderAdapter",
    "Go2RtcAdapter",
    "RingAdapter",
    "TempMediaContext",
    "TempMediaManager",
    "TranscodeError",
]
