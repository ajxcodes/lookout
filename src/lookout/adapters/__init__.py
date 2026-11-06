"""Lookout adapters layer."""

from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
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
    "RetentionResult",
    "SqliteConnectionManager",
    "SqliteEventRepository",
    "import_legacy_events",
]
