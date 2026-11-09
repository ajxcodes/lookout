"""Starter discovery probing for skeleton provider driver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lookout.providers.base import DiscoveryResult

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig
    from lookout.providers.templates.skeleton.driver import SkeletonProviderDriver


def probe_skeleton(
    driver: SkeletonProviderDriver, config: LookoutConfig | None = None
) -> DiscoveryResult:
    """Probe for skeleton provider availability or open ports."""
    is_open = driver.probe_socket("127.0.0.1", 8554)
    return DiscoveryResult(
        status="connected" if is_open else "disconnected",
        provider="skeleton",
        mode="standard",
        details="Skeleton template driver probe result",
        bridge_reachable=is_open,
        ports=[8080, 8554],
    )
