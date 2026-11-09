"""Discovery engine for pre-existing sidecars, streams, and bridges."""

from __future__ import annotations

import logging
from typing import Any, cast

from lookout.adapters.sidecar_registry import SidecarProviderRegistry
from lookout.config.schema import CameraConfig, LookoutConfig

logger = logging.getLogger(__name__)


class SidecarDiscoveryEngine:
    """Detects pre-existing camera bridges and adopts external streams."""

    def __init__(self, registry: SidecarProviderRegistry | None = None) -> None:
        self.registry = registry or SidecarProviderRegistry()

    def detect_pre_existing_sidecars(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Probe all registered providers for active local bridges or credentials."""
        report: dict[str, Any] = {}
        for p_info in self.registry.list_providers():
            pid = p_info["id"]
            try:
                provider = self.registry.get_provider(pid)
                res = provider.probe_existing(config)
                report[pid] = res.model_dump() if hasattr(res, "model_dump") else res
            except Exception as exc:
                logger.warning("Error probing provider '%s': %s", pid, exc)
                report[pid] = {
                    "status": "error",
                    "provider": pid,
                    "message": str(exc),
                }
        return report

    def adopt_stream(
        self,
        provider: str,
        stream_url: str,
        camera_id: str,
        camera_name: str,
        config: LookoutConfig,
    ) -> CameraConfig:
        """Adopt pre-existing or external stream into configuration."""
        adapter = self.registry.get_provider(provider)
        cam = adapter.adopt_stream(
            stream_url=stream_url,
            camera_id=camera_id,
            camera_name=camera_name,
            config=config,
        )
        return cast(CameraConfig, cam)
