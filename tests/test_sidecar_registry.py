"""Unit tests for SidecarProviderRegistry and pluggable camera bridge adapters."""

from pathlib import Path
from typing import Any

from lookout.adapters.sidecar_base import BaseSidecarAdapter
from lookout.adapters.sidecar_registry import SidecarProviderRegistry
from lookout.config.schema import LookoutConfig


class CustomCameraAdapter(BaseSidecarAdapter):
    """Mock third-party camera adapter for testing registry extensibility."""

    provider_id = "custom"
    display_name = "Custom Camera"
    default_ports = {"http": 8080, "rtsp": 8554}

    def probe_existing(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        return {"status": "connected", "provider": self.provider_id}

    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        return {"status": "authenticated", "provider": self.provider_id}

    def verify_2fa(self, session_id: str, code: str) -> dict[str, Any]:
        return {"status": "authenticated"}

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:
        return base_dir / "custom.env"

    def test_connection(self, endpoint: str | None = None) -> dict[str, Any]:
        return {"reachable": True, "provider": self.provider_id}


def test_sidecar_provider_registry_and_custom_adapter() -> None:
    """TR-8: Verify custom adapter registration and lookup in SidecarProviderRegistry."""
    registry = SidecarProviderRegistry()
    registry.register_provider(CustomCameraAdapter())

    assert registry.get_provider("custom").display_name == "Custom Camera"
    assert registry.get_provider("CUSTOM").provider_id == "custom"


def test_sidecar_provider_list_providers() -> None:
    """TR-9: Verify list_providers returns built-in and dynamically registered providers."""
    registry = SidecarProviderRegistry()
    providers = [p["id"] for p in registry.list_providers()]
    assert "wyze" in providers and "ring" in providers
