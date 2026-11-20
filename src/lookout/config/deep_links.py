"""Shared deep-link base resolution for ntfy notifications and HA sync entities.

Both consumers (the ntfy dispatcher in the web app and the Home Assistant sync
manager) must agree on a single viewer base so that existing notification links
keep working when entities are published to Home Assistant. Only the base lives
here; each caller appends the viewer query parameters it needs via
:func:`urllib.parse.urlencode`.
"""

from __future__ import annotations

from typing import Any

# Relative fallback so generated links are never empty when nothing is configured.
FALLBACK_DEEP_LINK_BASE = "/lookout/viewer"


def get_deep_link_base(config: Any | None) -> str | None:
    """Resolve the shared viewer deep-link base for *config*.

    Resolution order:

    1. ``config.home_assistant.ingress_url`` when set;
    2. ``<config.home_assistant_url>/lookout/viewer`` when ``home_assistant_url``
       is set (Home Assistant installs);
    3. ``config.public_url`` when set (plain Lookout browser link);
    4. ``None`` when none of the above are configured.

    Trailing slashes are stripped from the resolved value.
    """
    if config is None:
        return None

    ha_cfg = getattr(config, "home_assistant", None)
    ingress_url = getattr(ha_cfg, "ingress_url", None) if ha_cfg is not None else None
    if ingress_url:
        return str(ingress_url).rstrip("/")

    ha_url = getattr(config, "home_assistant_url", None)
    if ha_url:
        return f"{str(ha_url).rstrip('/')}/lookout/viewer"

    public_url = getattr(config, "public_url", None)
    if public_url:
        return str(public_url).rstrip("/")

    return None


__all__ = ["FALLBACK_DEEP_LINK_BASE", "get_deep_link_base"]
