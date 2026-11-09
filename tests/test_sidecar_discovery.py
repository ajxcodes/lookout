"""Unit tests for sidecar auto-discovery and pre-existing stream adoption."""

import os
from unittest.mock import patch

from lookout.adapters.sidecar_discovery import SidecarDiscoveryEngine
from lookout.config.schema import LookoutConfig


def test_auto_discovery_pre_authenticated_env() -> None:
    """TR-6: Verify environment variable credentials trigger pre_authenticated status."""
    engine = SidecarDiscoveryEngine()
    config = LookoutConfig(system_name="Discovery Test")

    env_vars = {"WYZE_EMAIL": "env_user@wyze.com", "RING_TOKEN": "env-ring-token"}
    with patch.dict(os.environ, env_vars):
        status = engine.detect_pre_existing_sidecars(config)
        wyze_status = status["wyze"]
        ring_status = status["ring"]

        assert (
            wyze_status["status"] == "pre_authenticated"
            and wyze_status["mode"] == "pre_authenticated"
        )
        assert (
            ring_status["status"] == "pre_authenticated"
            and ring_status["mode"] == "pre_authenticated"
        )


def test_adopt_pre_existing_stream() -> None:
    """TR-7: Verify adopting pre-existing stream creates CameraConfig and updates LookoutConfig."""
    engine = SidecarDiscoveryEngine()
    config = LookoutConfig(system_name="Adopt Test")

    adopted = engine.adopt_stream(
        provider="wyze",
        stream_url="rtsp://localhost:8554/front_camera",
        camera_id="adopted-wyze-1",
        camera_name="Adopted Front Wyze",
        config=config,
    )

    assert adopted.id == "adopted-wyze-1" and adopted.camera_type == "wyze"
    assert any(c.id == "adopted-wyze-1" for c in config.cameras)


def test_auto_discovery_wyze_api_key_env() -> None:
    """Verify WYZE_API_KEY triggers pre_authenticated status for Wyze."""
    engine = SidecarDiscoveryEngine()
    config = LookoutConfig(system_name="Discovery API Key Test")

    with patch.dict(os.environ, {"WYZE_API_KEY": "env-wyze-api-key"}):
        status = engine.detect_pre_existing_sidecars(config)
        assert status["wyze"]["status"] == "pre_authenticated"
