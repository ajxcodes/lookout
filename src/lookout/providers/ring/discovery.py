"""Port and existing stream probing for Ring camera bridge."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lookout.providers.base import DiscoveryResult

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig
    from lookout.providers.ring.driver import RingProviderDriver


def probe_ring(driver: RingProviderDriver, config: LookoutConfig | None = None) -> DiscoveryResult:
    """Detect existing Ring token in environment, config, or running ring-mqtt instance."""
    env_token = os.environ.get("RING_TOKEN") or os.environ.get("RINGTOKEN")
    has_config_token = (
        config is not None
        and config.ring is not None
        and bool(config.ring.refresh_token or config.ring.auth_token)
    )
    is_port_open = (
        driver.probe_socket("127.0.0.1", 55123)
        or driver.probe_socket("127.0.0.1", 8555)
        or driver.probe_socket("127.0.0.1", 8554)
    )

    remote_reachable = False
    if config is not None and config.ring is not None and config.ring.external_stream_url:
        res = driver.test_connection(config.ring.external_stream_url)
        remote_reachable = bool(
            res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
        )

    if env_token or has_config_token or is_port_open or remote_reachable:
        active_email = (
            os.environ.get("RING_EMAIL")
            or (config.ring.email if config and config.ring else None)
            or "ring_user@local"
        )
        return DiscoveryResult(
            status="pre_authenticated",
            provider="ring",
            mode="pre_authenticated",
            details="Pre-existing Ring token or ring-mqtt bridge detected",
            token_present=True,
            email=active_email,
            bridge_reachable=is_port_open or remote_reachable,
            ports=[55123, 8555],
        )

    return DiscoveryResult(
        status="disconnected",
        provider="ring",
        mode="unconfigured",
        details="No existing Ring bridge or credentials detected",
        ports=[55123, 8555],
    )
