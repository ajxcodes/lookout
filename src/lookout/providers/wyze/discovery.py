"""Port and existing stream probing for Wyze camera bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from lookout.providers.base import DiscoveryResult
from lookout.providers.credential_probe import wyze_credentials_present

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig
    from lookout.providers.wyze.driver import WyzeProviderDriver


def probe_wyze(
    driver: WyzeProviderDriver,
    config: LookoutConfig | None = None,
    *,
    sidecar_dir: str | Path = "data/sidecars",
) -> DiscoveryResult:
    """Detect existing Wyze credentials (config/env/sidecar) or a bridge."""
    wyze_cfg = getattr(config, "wyze", None) if config is not None else None
    if wyze_cfg is not None and bool(getattr(wyze_cfg, "logged_out", False)):
        return DiscoveryResult(
            status="logged_out",
            provider="wyze",
            mode="logged_out",
            details="Wyze credentials were cleared via logout",
            token_present=False,
            metadata={"source": "logged_out", "valid": False},
            ports=[5000, 8554],
        )

    probe = wyze_credentials_present(config=config, sidecar_dir=sidecar_dir)
    env_email = os.environ.get("WYZE_EMAIL")
    env_api_key = os.environ.get("WYZE_API_KEY") or os.environ.get("API_KEY")
    has_config = bool(
        wyze_cfg is not None
        and (wyze_cfg.email and (wyze_cfg.auth_token or wyze_cfg.password or wyze_cfg.api_key))
    )
    is_port_open = driver.probe_socket("127.0.0.1", 5000) or driver.probe_socket("127.0.0.1", 8554)

    remote_reachable = False
    if wyze_cfg is not None:
        if wyze_cfg.external_bridge_url:
            res = driver.test_connection(wyze_cfg.external_bridge_url)
            remote_reachable = bool(
                res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
            )
        if not remote_reachable and wyze_cfg.bridge_rtsp_url:
            res = driver.test_connection(wyze_cfg.bridge_rtsp_url)
            remote_reachable = bool(
                res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
            )

    if (
        probe["present"]
        or env_email
        or env_api_key
        or has_config
        or is_port_open
        or remote_reachable
    ):
        active_email = (
            env_email or (wyze_cfg.email if wyze_cfg is not None else None) or "wyze_user@local"
        )
        return DiscoveryResult(
            status="pre_authenticated",
            provider="wyze",
            mode="pre_authenticated",
            details="Pre-existing Wyze credentials or running bridge detected",
            email=active_email,
            token_present=bool(probe["present"]),
            bridge_reachable=is_port_open or remote_reachable,
            ports=[5000, 8554],
            metadata={"source": probe["source"], "valid": probe["valid"]},
        )

    return DiscoveryResult(
        status="disconnected",
        provider="wyze",
        mode="unconfigured",
        details="No existing Wyze bridge or credentials detected",
        ports=[5000, 8554],
        metadata={"source": probe["source"], "valid": probe["valid"]},
    )
