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
from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockTranscoderAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
)
from lookout.adapters.ring import RingAdapter
from lookout.adapters.sqlite import (
    AuditLogger,
    HouseholdMember,
    HouseholdMembersRepository,
    ImportResult,
    MediaRetentionWorker,
    RetentionResult,
    SqliteConnectionManager,
    SqliteEventRepository,
    import_legacy_events,
)
from lookout.adapters.tempfile import TempMediaContext, TempMediaManager
from lookout.adapters.web import SSEBroadcastHub, create_app

__all__ = [
    "AuditLogger",
    "CascadeVisionAdapter",
    "FFmpegNotFoundError",
    "FFmpegTranscoderAdapter",
    "FaceRecognitionAdapter",
    "GeminiVisionProvider",
    "Go2RtcAdapter",
    "HouseholdMember",
    "HouseholdMembersRepository",
    "ImmichGalleryAdapter",
    "ImportResult",
    "InMemoryEventRepository",
    "LocalEdgeVisionProvider",
    "MediaRetentionWorker",
    "MockAudioClassifierAdapter",
    "MockFaceRecognitionAdapter",
    "MockNotificationAdapter",
    "MockTranscoderAdapter",
    "MockVideoRecorderAdapter",
    "MockVisionInferenceAdapter",
    "OfflineVisionFallbackProvider",
    "OllamaVisionProvider",
    "OpenCodeVisionProvider",
    "RetentionResult",
    "RingAdapter",
    "SSEBroadcastHub",
    "SqliteConnectionManager",
    "SqliteEventRepository",
    "StructuredOutputParser",
    "TempMediaContext",
    "TempMediaManager",
    "TranscodeError",
    "YamnetAudioClassifierAdapter",
    "create_app",
    "import_legacy_events",
]
