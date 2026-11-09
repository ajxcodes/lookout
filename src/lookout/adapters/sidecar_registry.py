"""Registry for modular sidecar provider adapters delegating to ProviderRegistry."""

from __future__ import annotations

import logging
from typing import Any

from lookout.ports.sidecar_provider import SidecarProviderPort
from lookout.providers.base import BaseProviderDriver
from lookout.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


class SidecarProviderRegistry:
    """Registry maintaining available sidecar bridge provider adapters."""

    def __init__(self, provider_registry: ProviderRegistry | None = None) -> None:
        self._registry = provider_registry or ProviderRegistry()

    def register_provider(self, adapter: SidecarProviderPort | BaseProviderDriver | Any) -> None:
        """Register a provider adapter instance."""
        if isinstance(adapter, BaseProviderDriver):
            self._registry.register_driver(adapter)
        else:
            self._registry.register_provider(adapter)

    def get_provider(self, provider_id: str) -> Any:
        """Lookup provider adapter by id."""
        try:
            return self._registry.get_driver(provider_id)
        except KeyError as exc:
            raise KeyError(f"Sidecar provider '{provider_id}' is not registered.") from exc

    def list_providers(self) -> list[dict[str, Any]]:
        """Return metadata list of all registered provider adapters."""
        return self._registry.list_providers()
