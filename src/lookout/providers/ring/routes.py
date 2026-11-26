"""Provider-specific REST sub-router for Ring (/api/providers/ring/*)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from lookout.providers import auth_service

if TYPE_CHECKING:
    from lookout.providers.ring.driver import RingProviderDriver

logger = logging.getLogger(__name__)


class VerifyRequest(BaseModel):
    """Payload for 2FA challenge verification."""

    session_id: str = Field(..., description="Active 2FA session ID")
    code: str = Field(..., description="Verification code")


def _persist_path(request: Request) -> Path | None:
    config_path = getattr(request.app.state, "config_path", None)
    if config_path and Path(config_path).exists():
        return Path(config_path)
    return None


def _sidecar_dir(request: Request) -> Path:
    return Path(getattr(request.app.state, "sidecar_dir", "data/sidecars"))


def create_ring_router(driver: RingProviderDriver) -> APIRouter:
    """Create sub-router mounted under /api/providers/ring."""
    router = APIRouter()

    @router.get("/manifest")
    async def get_manifest() -> dict[str, Any]:
        return driver.get_ui_manifest().model_dump()

    @router.get("/status")
    async def get_status(request: Request) -> dict[str, Any]:
        active_config = getattr(request.app.state, "config", None)
        return driver.probe_existing(active_config).model_dump()

    @router.post("/login")
    async def login(credentials: dict[str, Any], request: Request) -> dict[str, Any]:
        result = auth_service.login(
            "ring",
            credentials,
            getattr(request.app.state, "config", None),
            driver=driver,
            force=bool(credentials.get("force", False)),
            sidecar_dir=_sidecar_dir(request),
        )
        return auth_service.sanitize_auth_result(result)

    @router.post("/verify")
    async def verify(payload: VerifyRequest, request: Request) -> dict[str, Any]:
        result = await auth_service.verify(
            "ring",
            payload.session_id,
            payload.code,
            getattr(request.app.state, "config", None),
            driver=driver,
            request_state=request.app.state,
            sidecar_dir=_sidecar_dir(request),
            config_path=_persist_path(request),
        )
        return auth_service.sanitize_auth_result(result)

    @router.post("/rotate")
    async def rotate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        config = getattr(request.app.state, "config", None)
        result = auth_service.login(
            "ring",
            payload,
            config,
            driver=driver,
            force=True,
            sidecar_dir=_sidecar_dir(request),
        )
        if result.status == "2fa_required" and payload.get("code"):
            result = await auth_service.verify(
                "ring",
                str(result.session_id),
                str(payload["code"]),
                config,
                driver=driver,
                request_state=request.app.state,
                sidecar_dir=_sidecar_dir(request),
                config_path=_persist_path(request),
            )
        return auth_service.sanitize_auth_result(result)

    @router.post("/logout")
    async def logout(request: Request) -> dict[str, Any]:
        result = auth_service.logout(
            "ring",
            getattr(request.app.state, "config", None),
            driver=driver,
            request_state=request.app.state,
            sidecar_dir=_sidecar_dir(request),
            config_path=_persist_path(request),
        )
        return auth_service.sanitize_auth_result(result)

    @router.post("/test")
    async def test(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = payload.get("endpoint") if payload else None
        return driver.test_connection(endpoint).model_dump()

    return router
