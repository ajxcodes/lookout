"""Port and existing stream probing for Ring camera bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from lookout.providers.base import DiscoveryResult
from lookout.providers.credential_probe import ring_credentials_present

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig
    from lookout.providers.ring.driver import RingProviderDriver


def probe_ring(
    driver: RingProviderDriver,
    config: LookoutConfig | None = None,
    *,
    sidecar_dir: str | Path = "data/sidecars",
) -> DiscoveryResult:
    """Detect existing Ring credentials (config/env/sidecar) or a bridge."""
    ring_cfg = getattr(config, "ring", None) if config is not None else None
    if ring_cfg is not None and bool(getattr(ring_cfg, "logged_out", False)):
        return DiscoveryResult(
            status="logged_out",
            provider="ring",
            mode="logged_out",
            details="Ring credentials were cleared via logout",
            token_present=False,
            metadata={"source": "logged_out", "valid": False},
            ports=[55123, 8555],
        )

    probe = ring_credentials_present(config=config, sidecar_dir=sidecar_dir)
    env_token = bool(os.environ.get("RING_TOKEN") or os.environ.get("RINGTOKEN"))
    has_config_token = bool(
        ring_cfg is not None and (ring_cfg.refresh_token or ring_cfg.auth_token)
    )
    is_port_open = (
        driver.probe_socket("127.0.0.1", 55123)
        or driver.probe_socket("127.0.0.1", 8555)
        or driver.probe_socket("127.0.0.1", 8554)
    )

    remote_reachable = False
    # Collector's getattr-based probing, using #64's None-guarded ``ring_cfg``.
    ext_url = getattr(ring_cfg, "external_stream_url", None)
    if ext_url:
        res = driver.test_connection(ext_url)
        remote_reachable = bool(
            res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
        )

    if probe["present"] or env_token or has_config_token or is_port_open or remote_reachable:
        active_email = (
            os.environ.get("RING_EMAIL")
            or (ring_cfg.email if ring_cfg is not None else None)
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
            metadata={"source": probe["source"], "valid": probe["valid"]},
        )

    return DiscoveryResult(
        status="disconnected",
        provider="ring",
        mode="unconfigured",
        details="No existing Ring bridge or credentials detected",
        ports=[55123, 8555],
        metadata={"source": probe["source"], "valid": probe["valid"]},
    )
