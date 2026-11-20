"""Tests for continuous recording configuration schema."""

from lookout.config.schema import CameraConfig, ContinuousRecordingConfig, LookoutConfig


def test_continuous_recording_config_defaults() -> None:
    cfg = ContinuousRecordingConfig()
    assert (
        cfg.enabled is True
        and cfg.segment_seconds == 60
        and cfg.retention_days == 1
        and cfg.max_storage_gb == 50.0
    )


def test_camera_config_continuous_recording_inherits_none() -> None:
    assert CameraConfig(id="c", name="c", stream_url="rtsp://x").continuous_recording is None


def test_lookout_config_has_continuous_block() -> None:
    cfg = LookoutConfig()
    assert isinstance(cfg.continuous_recording, ContinuousRecordingConfig)
