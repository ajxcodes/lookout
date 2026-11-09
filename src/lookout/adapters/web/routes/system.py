"""System diagnostics, edge hardware profiles, and benchmark REST endpoints."""

from __future__ import annotations

import logging
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
