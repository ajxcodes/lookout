"""Unit tests for Edge NPU adapters (Coral TPU, Hailo-8, MockNPU, EdgeNPURuntime)."""

from lookout.adapters.npu import (
    EdgeNPURuntime,
    GoogleCoralTPUAccelerator,
    Hailo8Accelerator,
    MockNPUAccelerator,
)


def test_edge_npu_auto_detection() -> None:
    runtime = EdgeNPURuntime()
    accelerator_names = runtime.list_available_accelerators()
    assert isinstance(accelerator_names, list)


def test_coral_tpu_accelerator_latency_under_20ms() -> None:
    coral = GoogleCoralTPUAccelerator()
    boxes, latency_ms = coral.infer(b"person at front door")
    assert latency_ms < 20.0 and len(boxes) >= 1


def test_hailo8_accelerator_detection_and_inference() -> None:
    hailo = Hailo8Accelerator()
    boxes, latency_ms = hailo.infer(b"person walking pet near vehicle")
    assert latency_ms < 20.0 and boxes[0].label in {"person", "vehicle", "pet"}


def test_mock_npu_accelerator() -> None:
    mock = MockNPUAccelerator(simulated_latency_ms=5.0)
    assert mock.is_available() is True
    boxes, latency_ms = mock.infer(b"dog in yard")
    assert latency_ms < 20.0
    assert len(boxes) >= 1
