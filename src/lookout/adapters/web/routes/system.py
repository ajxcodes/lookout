"""System diagnostics, edge hardware profiles, and benchmark REST endpoints."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lookout.adapters.benchmark import BenchmarkEngine
from lookout.config.profiles import (
    EDGE_PROFILES,
    apply_edge_profile,
    detect_recommended_profile,
)
from lookout.config.schema import LookoutConfig

logger = logging.getLogger(__name__)

system_router = APIRouter(prefix="/system", tags=["system"])


class ApplyProfileRequest(BaseModel):
    """Payload to apply an edge hardware sizing profile."""

    profile: str = Field(..., description="Profile identifier to apply")


@system_router.get("/profiles")
async def list_profiles() -> dict[str, Any]:
    """List all available edge hardware sizing profiles and detected recommendation."""
    recommended = detect_recommended_profile()
    profiles_list = list(EDGE_PROFILES.values())
    return {
        "profiles": profiles_list,
        "recommended": recommended,
    }


@system_router.get("/benchmark")
async def get_system_benchmark(request: Request) -> dict[str, Any]:
    """Execute quick empirical hardware diagnostics benchmark."""
    config: LookoutConfig = getattr(request.app.state, "config", None) or LookoutConfig()
    engine = BenchmarkEngine(config=config)
    report = await engine.run_quick_benchmark()
    return report.model_dump()


@system_router.post("/profile")
async def apply_profile(payload: ApplyProfileRequest, request: Request) -> dict[str, Any]:
    """Apply an edge sizing profile to runtime configuration."""
    profile_id = payload.profile
    if profile_id not in EDGE_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown profile '{profile_id}'. Available: {list(EDGE_PROFILES.keys())}",
        )

    config: LookoutConfig = getattr(request.app.state, "config", None) or LookoutConfig()
    updated = apply_edge_profile(config, profile_id)
    request.app.state.config = updated

    return {
        "status": "applied",
        "profile": profile_id,
        "transcode": updated.transcode.model_dump(),
        "light_ml": updated.light_ml.model_dump(),
    }


# ---------------------------------------------------------------------------
# Selective reset / factory-restore endpoints (two-phase: preview then execute)
# ---------------------------------------------------------------------------

_ALL_SCOPES = [
    "camera_config",
    "provider_auth",
    "notification_settings",
    "storage_settings",
    "event_history",
    "media_files",
]

_RESET_SCOPES = {
    "camera_config": "camera_config",
    "cameras": "camera_config",
    "camera_configuration": "camera_config",
    "provider_auth": "provider_auth",
    "providers": "provider_auth",
    "provider_authentication": "provider_auth",
    "notification_settings": "notification_settings",
    "notifications": "notification_settings",
    "storage_settings": "storage_settings",
    "storage": "storage_settings",
    "event_history": "event_history",
    "events": "event_history",
    "media_files": "media_files",
    "media": "media_files",
    "all": _ALL_SCOPES,
    "full": _ALL_SCOPES,
    "factory_reset": _ALL_SCOPES,
}


def _normalize_scope(scope: Any) -> Any:
    if isinstance(scope, list):
        return [_normalize_scope(s) for s in scope]
    if scope is None:
        return None
    key = str(scope).strip().lower()
    return _RESET_SCOPES.get(key, key)


class ResetPreviewRequest(BaseModel):
    scope: Any = Field(default=None, description="Reset scope (or list of scopes)")


class ResetExecuteRequest(BaseModel):
    confirm: str = Field(..., description="Must equal 'RESET'")
    scope: Any = Field(default=None, description="Reset scope (or list of scopes)")


def _impact_summary(scope: Any) -> dict[str, Any]:
    normalized = _normalize_scope(scope)
    if isinstance(normalized, list):
        scopes = normalized
    elif normalized:
        scopes = [normalized]
    else:
        scopes = list(_RESET_SCOPES.values())
    # de-dup while preserving order
    seen = set()
    uniq = []
    for s in scopes:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    summaries = {
        "camera_config": "Remove all cameras (provider authentication preserved).",
        "provider_auth": "Logout all providers (camera configuration preserved).",
        "notification_settings": "Restore notification settings to defaults for all cameras.",
        "storage_settings": "Restore storage settings to defaults.",
        "event_history": "Clear all event history.",
        "media_files": "Clear all media files.",
    }
    impacts = [summaries.get(s, f"Reset scope '{s}'.") for s in uniq]
    return {
        "scope": normalized if normalized is not None else uniq,
        "scopes": uniq,
        "summary": " ".join(impacts),
        "impact_summary": " ".join(impacts),
        "impact": impacts,
        "details": impacts,
    }


async def _execute_reset(scope: Any, request: Request) -> dict[str, Any]:
    from lookout.config.schema import StorageConfig

    normalized = _normalize_scope(scope)
    scopes = normalized if isinstance(normalized, list) else ([normalized] if normalized else [])
    if not scopes:
        return {"status": "ok", "scopes": []}

    config = getattr(request.app.state, "config", None)
    repo = getattr(request.app.state, "repository", None)
    config_path = getattr(request.app.state, "config_path", None)

    for s in scopes:
        if s == "camera_config":
            if config is not None and hasattr(config, "cameras"):
                config.cameras = []
            if config_path and config is not None:
                try:
                    from lookout.providers import config_io

                    config_io.atomic_write_config(config, config_path)
                except Exception:
                    pass
        elif s == "provider_auth":
            if config is not None:
                for section_name in ("ring", "wyze"):
                    section = getattr(config, section_name, None)
                    if section is not None:
                        with contextlib.suppress(Exception):
                            section.logged_out = True
                        for attr in ("refresh_token", "auth_token", "access_token"):
                            if hasattr(section, attr):
                                with contextlib.suppress(Exception):
                                    setattr(section, attr, None)
            if config_path and config is not None:
                try:
                    from lookout.providers import config_io

                    config_io.atomic_write_config(config, config_path)
                except Exception:
                    pass
        elif s == "notification_settings":
            if config is not None and hasattr(config, "cameras"):
                for cam in config.cameras:
                    try:
                        cam.notifications_enabled = True
                        cam.notifications_muted = False
                        cam.notification_min_threat = None
                        cam.notification_event_types = ["motion", "sound", "person", "ding"]
                        cam.notify_pass1 = True
                        cam.notify_pass2 = True
                    except Exception:
                        pass
            if config_path and config is not None:
                try:
                    from lookout.providers import config_io

                    config_io.atomic_write_config(config, config_path)
                except Exception:
                    pass
        elif s == "storage_settings":
            if config is not None and hasattr(config, "storage"):
                config.storage = StorageConfig()
            if config_path and config is not None:
                try:
                    from lookout.providers import config_io

                    config_io.atomic_write_config(config, config_path)
                except Exception:
                    pass
        elif s == "event_history":
            if repo is not None:
                try:
                    events = await repo.find(limit=1000000)
                    for ev in events:
                        await repo.delete(ev.id)
                except Exception:
                    pass
        elif s == "media_files" and config is not None and hasattr(config, "storage"):
            base = getattr(config.storage, "base_path", None)
            if base:
                try:
                    for root, _dirs, files in os.walk(base):
                        for f in files:
                            with contextlib.suppress(Exception):
                                os.remove(os.path.join(root, f))
                except Exception:
                    pass

    return {"status": "ok", "scopes": scopes}


@system_router.post("/reset/preview")
@system_router.post("/reset/preview/")
async def reset_preview(
    payload: ResetPreviewRequest | None = None, request: Request = None
) -> dict:
    scope = payload.scope if payload is not None else None
    return _impact_summary(scope)


@system_router.post("/reset/execute")
@system_router.post("/reset/execute/")
async def reset_execute(
    payload: ResetExecuteRequest, request: Request
) -> dict:
    if payload.confirm != "RESET":
        raise HTTPException(status_code=400, detail="Confirmation token must be 'RESET'")
    return await _execute_reset(payload.scope, request)


# Alias router exposing the same endpoints under /api/reset/...
reset_router = APIRouter(tags=["reset"])


@reset_router.post("/reset/preview")
@reset_router.post("/reset/preview/")
async def reset_preview_alias(
    payload: ResetPreviewRequest | None = None, request: Request = None
) -> dict:
    scope = payload.scope if payload is not None else None
    return _impact_summary(scope)


@reset_router.post("/reset/execute")
@reset_router.post("/reset/execute/")
async def reset_execute_alias(
    payload: ResetExecuteRequest, request: Request
) -> dict:
    if payload.confirm != "RESET":
        raise HTTPException(status_code=400, detail="Confirmation token must be 'RESET'")
    return await _execute_reset(payload.scope, request)
