"""Port and existing stream probing for Wyze camera bridge."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lookout.providers.base import DiscoveryResult

if TYPE_CHECKING:
    from lookout.config.schema import LookoutConfig
    from lookout.providers.wyze.driver import WyzeProviderDriver


def probe_wyze(driver: WyzeProviderDriver, config: LookoutConfig | None = None) -> DiscoveryResult:
    """Detect existing Wyze bridge, container environment variables, or config tokens."""
    env_email = os.environ.get("WYZE_EMAIL")
    env_token = os.environ.get("WYZE_ACCESS_TOKEN")
    env_api_key = os.environ.get("WYZE_API_KEY") or os.environ.get("API_KEY")
    has_config = (
        config is not None
        and config.wyze is not None
        and bool(
            config.wyze.email
            and (config.wyze.auth_token or config.wyze.password or config.wyze.api_key)
        )
    )
    is_port_open = driver.probe_socket("127.0.0.1", 5000) or driver.probe_socket("127.0.0.1", 8554)

    remote_reachable = False
    if config is not None and config.wyze is not None:
        if config.wyze.external_bridge_url:
            res = driver.test_connection(config.wyze.external_bridge_url)
            remote_reachable = bool(
                res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
            )
        if not remote_reachable and config.wyze.bridge_rtsp_url:
            res = driver.test_connection(config.wyze.bridge_rtsp_url)
            remote_reachable = bool(
                res.get("reachable") if hasattr(res, "get") else getattr(res, "reachable", False)
            )

    if env_email or env_token or env_api_key or has_config or is_port_open or remote_reachable:
        active_email = (
            env_email
            or (config.wyze.email if config and config.wyze else None)
            or "wyze_user@local"
        )
        return DiscoveryResult(
            status="pre_authenticated",
            provider="wyze",
            mode="pre_authenticated",
            details="Pre-existing Wyze credentials or running bridge detected",
            email=active_email,
            token_present=bool(
                env_token or (config.wyze.auth_token if config and config.wyze else False)
            ),
            bridge_reachable=is_port_open or remote_reachable,
            ports=[5000, 8554],
        )

    return DiscoveryResult(
        status="disconnected",
        provider="wyze",
        mode="unconfigured",
        details="No existing Wyze bridge or credentials detected",
        ports=[5000, 8554],
    )
