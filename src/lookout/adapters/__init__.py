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
from lookout.adapters.immich import ImmichGalleryAdapter

__all__ = [
    "CascadeVisionAdapter",
    "FaceRecognitionAdapter",
    "GeminiVisionProvider",
    "ImmichGalleryAdapter",
    "LocalEdgeVisionProvider",
    "OfflineVisionFallbackProvider",
    "OllamaVisionProvider",
    "OpenCodeVisionProvider",
    "StructuredOutputParser",
    "YamnetAudioClassifierAdapter",
]
