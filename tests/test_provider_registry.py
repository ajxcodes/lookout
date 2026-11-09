"""Unit tests for ProviderRegistry auto-discovery and custom driver registration."""

from pathlib import Path
from typing import Any

from lookout.config.schema import LookoutConfig
from lookout.providers.base import (
    AuthResult,
    BaseProviderDriver,
    DiscoveryResult,
    ProviderManifest,
    StreamDescriptor,
)
from lookout.providers.registry import ProviderRegistry


class CustomCameraDriver(BaseProviderDriver):
    """Custom camera driver for testing pluggable registration."""

    provider_id = "custom"
    display_name = "Custom Camera Driver"
    default_ports = {"http": 8080, "rtsp": 8554}

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        return AuthResult(status="authenticated", provider=self.provider_id)

    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        return AuthResult(status="authenticated", provider=self.provider_id)

    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult:
        return DiscoveryResult(status="connected", provider=self.provider_id)

    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]:
        p = Path(target_dir) / "custom.env"
        p.write_text("CUSTOM=1\n", encoding="utf-8")
        return [p]

    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]:
        return []

    def get_ui_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            id=self.provider_id,
            name=self.provider_id,
            display_name=self.display_name,
            description="Custom Driver",
            default_ports=self.default_ports,
        )


def test_provider_registry_auto_discovery() -> None:
    """TR-6: Verify automatic discovery of built-in providers (Wyze & Ring)."""
    registry = ProviderRegistry()
    manifests = [m.id for m in registry.list_manifests()]
    assert "wyze" in manifests and "ring" in manifests


def test_provider_registry_custom_driver_registration() -> None:
    """TR-7: Verify registration and lookup of custom camera drivers."""
    registry = ProviderRegistry()
    registry.register_driver(CustomCameraDriver())

    assert registry.get_driver("custom").display_name == "Custom Camera Driver"
    assert registry.get_driver("CUSTOM").provider_id == "custom"

    manifest_ids = [m.id for m in registry.list_manifests()]
    assert "custom" in manifest_ids
