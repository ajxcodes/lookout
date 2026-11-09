"""Provider-specific REST sub-router for skeleton driver (/api/providers/skeleton/*)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from lookout.providers.templates.skeleton.driver import SkeletonProviderDriver

logger = logging.getLogger(__name__)


def create_skeleton_router(driver: SkeletonProviderDriver) -> APIRouter:
    """Create sub-router mounted under /api/providers/skeleton."""
    router = APIRouter()

    @router.get("/manifest")
    async def get_manifest() -> dict[str, Any]:
        return driver.get_ui_manifest().model_dump()

    @router.get("/status")
    async def get_status(request: Request) -> dict[str, Any]:
        active_config = getattr(request.app.state, "config", None)
        return driver.probe_existing(active_config).model_dump()

    @router.post("/login")
    async def login(credentials: dict[str, Any]) -> dict[str, Any]:
        return driver.authenticate(credentials).model_dump()

    @router.post("/test")
    async def test(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = payload.get("endpoint") if payload else None
        return driver.test_connection(endpoint).model_dump()

    return router
