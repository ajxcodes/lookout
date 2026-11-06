"""Lookout adapters layer."""

from lookout.adapters.ai_cascade import (
    CascadeVisionAdapter,
    GeminiVisionProvider,
    LocalEdgeVisionProvider,
    OfflineVisionFallbackProvider,
    OllamaVisionProvider,
    OpenCodeVisionProvider,
    StructuredOutputParser,
)
from lookout.adapters.audio_classifier import YamnetAudioClassifierAdapter
from lookout.adapters.face_recognition import FaceRecognitionAdapter
from lookout.adapters.ffmpeg import FFmpegNotFoundError, FFmpegTranscoderAdapter, TranscodeError
from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.immich import ImmichGalleryAdapter
from lookout.adapters.ring import RingAdapter
from lookout.adapters.tempfile import TempMediaContext, TempMediaManager

__all__ = [
    "CascadeVisionAdapter",
    "FFmpegNotFoundError",
    "FFmpegTranscoderAdapter",
    "FaceRecognitionAdapter",
    "GeminiVisionProvider",
    "Go2RtcAdapter",
    "ImmichGalleryAdapter",
    "LocalEdgeVisionProvider",
    "OfflineVisionFallbackProvider",
    "OllamaVisionProvider",
    "OpenCodeVisionProvider",
    "RingAdapter",
    "StructuredOutputParser",
    "TempMediaContext",
    "TempMediaManager",
    "TranscodeError",
    "YamnetAudioClassifierAdapter",
]
