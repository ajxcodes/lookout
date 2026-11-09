"""REST API endpoints for stream provider manifests and dynamic sub-router mounting."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from lookout.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

registry = ProviderRegistry()
router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
@router.get("/")
async def list_providers() -> dict[str, Any]:
    """List all registered stream provider UI manifests."""
    manifests = [m.model_dump() for m in registry.list_manifests()]
    return {"providers": manifests}


@router.get("/{provider_id}")
async def get_provider(provider_id: str, request: Request) -> dict[str, Any]:
    """Return manifest and current status for a single provider."""
    try:
        driver = registry.get_driver(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    active_config = getattr(request.app.state, "config", None)
    return {
        "manifest": driver.get_ui_manifest().model_dump(),
        "status": driver.probe_existing(active_config).model_dump(),
    }


# Mount dynamic provider sub-routers (e.g. /providers/wyze/manifest, /providers/ring/login, etc.)
router.include_router(registry.create_combined_router())
