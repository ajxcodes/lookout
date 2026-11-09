"""Registry for modular sidecar provider adapters."""

import logging
from typing import Any

from lookout.adapters.sidecar_auth import RingSidecarAdapter, WyzeSidecarAdapter
from lookout.ports.sidecar_provider import SidecarProviderPort

logger = logging.getLogger(__name__)


class SidecarProviderRegistry:
    """Registry maintaining available sidecar bridge provider adapters."""

    def __init__(self) -> None:
        self._providers: dict[str, SidecarProviderPort] = {}
        # Built-in registrations
        self.register_provider(WyzeSidecarAdapter())
        self.register_provider(RingSidecarAdapter())

    def register_provider(self, adapter: SidecarProviderPort) -> None:
        """Register a provider adapter instance."""
        provider_id = adapter.provider_id.lower()
        self._providers[provider_id] = adapter
        logger.debug("Registered sidecar provider: %s (%s)", provider_id, adapter.display_name)

    def get_provider(self, provider_id: str) -> SidecarProviderPort:
        """Lookup provider adapter by id."""
        key = provider_id.lower()
        if key not in self._providers:
            raise KeyError(f"Sidecar provider '{provider_id}' is not registered.")
        return self._providers[key]

    def list_providers(self) -> list[dict[str, Any]]:
        """Return metadata list of all registered provider adapters."""
        return [
            {
                "id": adapter.provider_id,
                "name": adapter.display_name,
                "display_name": adapter.display_name,
                "default_ports": getattr(adapter, "default_ports", {}),
            }
            for adapter in self._providers.values()
        ]
