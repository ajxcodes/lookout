"""Unit tests for PTZ camera configuration fields."""

import pytest
from pydantic import ValidationError

from lookout.config.schema import CameraConfig


def test_camera_config_ptz_fields_defaults() -> None:
    """TR-6: PTZ fields default to disabled."""
    cam = CameraConfig(id="cam1", name="Cam 1", stream_url="rtsp://localhost:8554/cam1")
    assert cam.ptz_enabled is False


def test_camera_config_ptz_protocol_validation() -> None:
    """TR-7: Invalid PTZ protocol raises ValidationError."""
    with pytest.raises(ValidationError):
        CameraConfig(
            id="cam1",
            name="Cam 1",
            stream_url="rtsp://localhost:8554/cam1",
            ptz_protocol="invalid",
        )


def test_camera_config_ptz_max_speed_clamped() -> None:
    """TR-8: Out-of-range ptz_max_speed raises ValidationError."""
    with pytest.raises(ValidationError):
        CameraConfig(
            id="cam1",
            name="Cam 1",
            stream_url="rtsp://localhost:8554/cam1",
            ptz_max_speed=1.5,
        )
