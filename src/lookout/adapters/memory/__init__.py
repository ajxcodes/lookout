"""In-memory mock adapters for testing and standalone execution."""

from lookout.adapters.memory.audio import MockAudioClassifierAdapter
from lookout.adapters.memory.face import MockFaceRecognitionAdapter
from lookout.adapters.memory.notification import MockNotificationAdapter
from lookout.adapters.memory.ptz import MockPtzControlAdapter
from lookout.adapters.memory.repository import InMemoryEventRepository
from lookout.adapters.memory.transcode import MockTranscoderAdapter
from lookout.adapters.memory.video import MockVideoRecorderAdapter
from lookout.adapters.memory.vision import MockVisionInferenceAdapter

__all__ = [
    "InMemoryEventRepository",
    "MockAudioClassifierAdapter",
    "MockFaceRecognitionAdapter",
    "MockNotificationAdapter",
    "MockTranscoderAdapter",
    "MockPtzControlAdapter",
    "MockVideoRecorderAdapter",
    "MockVisionInferenceAdapter",
]
