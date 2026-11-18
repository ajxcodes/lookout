"""REST API endpoints for stream provider manifests and dynamic sub-router mounting."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from lookout.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

registry = ProviderRegistry()
router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/status")
async def providers_status(request: Request) -> dict[str, Any]:
    active_config = getattr(request.app.state, "config", None)
    manifests = []
    for d in registry.list_drivers() if hasattr(registry, "list_drivers") else []:
        try:
            manifests.append(
                {"id": d.provider_id, "status": d.probe_existing(active_config).model_dump()}
            )
        except Exception as exc:
            manifests.append({"id": getattr(d, "provider_id", "?"), "error": str(exc)})

    native_block: dict[str, Any] = {
        "wyze": {
            "native_ingestion": bool(
                getattr(getattr(active_config, "wyze", None), "native_ingestion", False)
            ),
            "configured": bool(
                getattr(getattr(active_config, "wyze", None), "auth_token", None)
                or getattr(getattr(active_config, "wyze", None), "refresh_token", None)
                or getattr(getattr(active_config, "wyze", None), "email", None)
            ),
        },
        "ring": {
            "native_ingestion": bool(
                getattr(getattr(active_config, "ring", None), "native_ingestion", False)
            ),
            "configured": bool(
                getattr(getattr(active_config, "ring", None), "refresh_token", None)
                or getattr(getattr(active_config, "ring", None), "auth_token", None)
            ),
        },
    }
    manager = getattr(request.app.state, "native_stream_manager", None)
    if manager is not None:
        from lookout.adapters.web.app import _redact_source

        manager_status = await manager.status()
        streams = manager_status.get("streams")
        if isinstance(streams, dict):
            manager_status["streams"] = {
                name: _redact_source(source) for name, source in streams.items()
            }
        native_block["stream_manager"] = manager_status
    poller = getattr(request.app.state, "native_event_poller", None)
    native_block["event_poller_running"] = bool(getattr(poller, "running", False))
    poller_status = None
    if poller is not None:
        try:
            raw = poller.status() if callable(getattr(poller, "status", None)) else None
            if raw is not None and hasattr(raw, "__await__"):
                raw = await raw
            poller_status = raw
        except Exception:  # noqa: BLE001
            poller_status = None
    native_block["event_poller"] = poller_status
    native_block["event_poller_last_error"] = getattr(poller, "last_error", None)
    native_block["event_poller_last_success"] = getattr(poller, "last_success", None)
    native_block["event_poller_last_event_count"] = getattr(poller, "last_event_count", 0)
    event_poller_errors: list[str] = []
    event_poller_errors_by_camera: dict[str, Any] = {}
    if isinstance(poller_status, dict):
        for container_key in ("cameras", "health", "per_camera"):
            container = poller_status.get(container_key)
            if isinstance(container, dict):
                for cam_id, health in container.items():
                    if isinstance(health, dict) and health.get("last_error"):
                        event_poller_errors.append(str(health["last_error"]))
                        event_poller_errors_by_camera[str(cam_id)] = health.get("last_error")
    native_block["event_poller_errors"] = event_poller_errors
    native_block["event_poller_errors_by_camera"] = event_poller_errors_by_camera

    return {"providers": manifests, "native": native_block}


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
