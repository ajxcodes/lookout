"""Unit tests for BaseProviderDriver, data schemas, and session lifecycle caching."""

import time
from pathlib import Path

from lookout.config.schema import LookoutConfig
from lookout.providers.base import (
    AuthResult,
    BaseProviderDriver,
    ConnectionResult,
    DiscoveryResult,
    ProviderManifest,
    StreamDescriptor,
)


class DummyDriver(BaseProviderDriver):
    provider_id = "wyze"
    display_name = "Wyze Cam Bridge"
    default_ports = {"http": 5000, "rtsp": 8554}

    def authenticate(self, credentials):
        return AuthResult(status="authenticated", provider=self.provider_id)

    def verify_2fa(self, session_id, code):
        return AuthResult(status="authenticated", provider=self.provider_id)

    def probe_existing(self, config=None):
        return DiscoveryResult(status="connected", provider=self.provider_id)

    def sync_environment(self, config, target_dir):
        return [Path(target_dir) / "dummy.env"]

    def get_stream_urls(self, config=None):
        return [
            StreamDescriptor(
                stream_url="rtsp://localhost:8554/cam1",
                camera_id="cam1",
                camera_name="Cam 1",
            )
        ]

    def get_ui_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            id=self.provider_id,
            name=self.provider_id,
            display_name=self.display_name,
            description="Wyze bridge driver",
            default_ports=self.default_ports,
        )


def test_provider_models_and_session_caching() -> None:
    """TR-5: Test provider models, manifest, and session expiration caching."""
    driver = DummyDriver(session_ttl_seconds=0.1)
    manifest = driver.get_ui_manifest()
    assert manifest.id == "wyze" and manifest.display_name == "Wyze Cam Bridge"

    # Test dict-like indexing and model attributes
    auth = AuthResult(status="2fa_required", provider="wyze", session_id="s123")
    assert auth["status"] == "2fa_required"
    assert auth.session_id == "s123"
    assert "session_id" in auth

    disc = DiscoveryResult(status="connected", provider="wyze", bridge_reachable=True)
    assert disc["status"] == "connected"
    assert disc.bridge_reachable is True

    conn = ConnectionResult(reachable=True, provider="wyze")
    assert conn["reachable"] is True

    # Test session caching & pruning
    driver.store_session("sess1", {"token": "abc"})
    assert driver.get_session("sess1") == {
        "token": "abc",
        "created_at": driver.get_session("sess1")["created_at"],
    }

    time.sleep(0.15)
    assert driver.get_session("sess1") is None

    # Test stream adoption
    cfg = LookoutConfig()
    cam = driver.adopt_stream("rtsp://localhost:8554/cam1", "cam-1", "Porch Cam", cfg)
    assert cam.id == "cam-1" and len(cfg.cameras) == 1
