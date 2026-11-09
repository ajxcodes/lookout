"""Synchronizer for sidecar container environment files (.env)."""

from __future__ import annotations

import logging
from pathlib import Path

from lookout.adapters.sidecar_registry import SidecarProviderRegistry
from lookout.config.schema import LookoutConfig

logger = logging.getLogger(__name__)


class SidecarConfigSynchronizer:
    """Manages synchronization of container environment files for bridge sidecars."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        registry: SidecarProviderRegistry | None = None,
    ) -> None:
        if base_dir is None:
            self.base_dir = Path("data/sidecars")
        else:
            self.base_dir = Path(base_dir)
        self.registry = registry or SidecarProviderRegistry()

    def sync_all(self, config: LookoutConfig) -> dict[str, Path]:
        """Synchronize environment files for all registered sidecar providers."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, Path] = {}
        for p_info in self.registry.list_providers():
            pid = p_info["id"]
            try:
                provider = self.registry.get_provider(pid)
                res = provider.sync_environment(config, self.base_dir)
                path = (
                    res[0]
                    if isinstance(res, list) and res
                    else (res if isinstance(res, Path) else self.base_dir)
                )
                results[pid] = path
                logger.info("Synchronized environment for provider '%s' to %s", pid, path)
            except Exception as exc:
                logger.warning("Failed to sync environment for provider '%s': %s", pid, exc)
        return results

    def sync_provider(self, provider_id: str, config: LookoutConfig) -> Path:
        """Synchronize environment file for a single registered sidecar provider."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        provider = self.registry.get_provider(provider_id)
        res = provider.sync_environment(config, self.base_dir)
        return (
            res[0]
            if isinstance(res, list) and res
            else (res if isinstance(res, Path) else self.base_dir)
        )
