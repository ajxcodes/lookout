"""Lookout adapters layer."""

from lookout.adapters.fastapi_ingress import (
    FastAPIIngressAdapter,
    app,
    create_app,
)
from lookout.adapters.home_assistant import (
    HAEgressAdapter,
    HomeAssistantAdapter,
    HomeAssistantEgressAdapter,
)
from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
)
from lookout.adapters.ntfy import (
    NtfyAdapter,
    NtfyDispatcher,
    NtfyNotificationAdapter,
)
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

__all__ = [
    "AuditLogger",
    "FastAPIIngressAdapter",
    "HAEgressAdapter",
    "HomeAssistantAdapter",
    "HomeAssistantEgressAdapter",
    "HouseholdMember",
    "HouseholdMembersRepository",
    "ImportResult",
    "InMemoryEventRepository",
    "MediaRetentionWorker",
    "MockAudioClassifierAdapter",
    "MockFaceRecognitionAdapter",
    "MockNotificationAdapter",
    "MockVideoRecorderAdapter",
    "MockVisionInferenceAdapter",
    "NtfyAdapter",
    "NtfyDispatcher",
    "NtfyNotificationAdapter",
    "RetentionResult",
    "SqliteConnectionManager",
    "SqliteEventRepository",
    "app",
    "create_app",
    "import_legacy_events",
]
