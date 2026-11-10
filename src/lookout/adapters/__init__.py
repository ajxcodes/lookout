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
from lookout.adapters.fastapi_ingress import (
    FastAPIIngressAdapter,
    app,
)
from lookout.adapters.fastapi_ingress import (
    create_app as create_ingress_app,
)
from lookout.adapters.ffmpeg import FFmpegNotFoundError, FFmpegTranscoderAdapter, TranscodeError
from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.go2rtc_ptz import Go2RtcPtzAdapter
from lookout.adapters.home_assistant import (
    HAEgressAdapter,
    HomeAssistantAdapter,
    HomeAssistantEgressAdapter,
)
from lookout.adapters.immich import ImmichGalleryAdapter
from lookout.adapters.light_ml import (
    ClipAnalysisResult,
    IlluminationFilter,
    LightMLObjectDetector,
    PetPostureClassifier,
    SpatialZoneEvaluator,
    analyze_clip_frames,
)
from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockPtzControlAdapter,
    MockTranscoderAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
)
from lookout.adapters.mqtt_ptz import MqttWyzePtzAdapter
from lookout.adapters.ntfy import (
    NtfyAdapter,
    NtfyDispatcher,
    NtfyNotificationAdapter,
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
from lookout.adapters.storage_service import NoOpMediaArchivalAdapter, StorageService
from lookout.adapters.tempfile import TempMediaContext, TempMediaManager
from lookout.adapters.web import SSEBroadcastHub, create_app

__all__ = [
    "AuditLogger",
    "CascadeVisionAdapter",
    "ClipAnalysisResult",
    "FastAPIIngressAdapter",
    "FFmpegNotFoundError",
    "FFmpegTranscoderAdapter",
    "FaceRecognitionAdapter",
    "GeminiVisionProvider",
    "Go2RtcAdapter",
    "Go2RtcPtzAdapter",
    "MqttWyzePtzAdapter",
    "HAEgressAdapter",
    "HomeAssistantAdapter",
    "HomeAssistantEgressAdapter",
    "HouseholdMember",
    "HouseholdMembersRepository",
    "IlluminationFilter",
    "ImmichGalleryAdapter",
    "ImportResult",
    "InMemoryEventRepository",
    "LightMLObjectDetector",
    "LocalEdgeVisionProvider",
    "MediaRetentionWorker",
    "MockAudioClassifierAdapter",
    "MockFaceRecognitionAdapter",
    "MockNotificationAdapter",
    "MockPtzControlAdapter",
    "MockTranscoderAdapter",
    "MockVideoRecorderAdapter",
    "MockVisionInferenceAdapter",
    "NoOpMediaArchivalAdapter",
    "NtfyAdapter",
    "NtfyDispatcher",
    "NtfyNotificationAdapter",
    "OfflineVisionFallbackProvider",
    "OllamaVisionProvider",
    "OpenCodeVisionProvider",
    "PetPostureClassifier",
    "RetentionResult",
    "RingAdapter",
    "SSEBroadcastHub",
    "SpatialZoneEvaluator",
    "SqliteConnectionManager",
    "SqliteEventRepository",
    "StorageService",
    "StructuredOutputParser",
    "TempMediaContext",
    "TempMediaManager",
    "TranscodeError",
    "YamnetAudioClassifierAdapter",
    "analyze_clip_frames",
    "app",
    "create_app",
    "create_ingress_app",
    "import_legacy_events",
]
