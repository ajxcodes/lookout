"""Provider-specific REST sub-router for Wyze (/api/providers/wyze/*)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lookout.config.schema import WyzeConfig

if TYPE_CHECKING:
    from lookout.providers.wyze.driver import WyzeProviderDriver

logger = logging.getLogger(__name__)


class VerifyRequest(BaseModel):
    """Payload for 2FA challenge verification."""

    session_id: str = Field(..., description="Active 2FA session ID")
    code: str = Field(..., description="Verification code")


def create_wyze_router(driver: WyzeProviderDriver) -> APIRouter:
    """Create sub-router mounted under /api/providers/wyze."""
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
        res = driver.authenticate(credentials)
        if res.status == "error":
            raise HTTPException(status_code=400, detail=res.message or "Login failed")
        return res.model_dump()

    @router.post("/verify")
    async def verify(payload: VerifyRequest, request: Request) -> dict[str, Any]:
        res = driver.verify_2fa(payload.session_id, payload.code)
        if res.status == "authenticated":
            active_config = getattr(request.app.state, "config", None)
            if active_config is not None:
                if active_config.wyze is None:
                    active_config.wyze = WyzeConfig()
                active_config.wyze.auth_token = res.auth_token
                active_config.wyze.refresh_token = res.refresh_token
                if res.email:
                    active_config.wyze.email = res.email

                # Synchronize environment
                driver.sync_environment(active_config, Path("data/sidecars"))

                config_path = getattr(request.app.state, "config_path", None)
                if config_path and Path(config_path).exists():
                    Path(config_path).write_text(
                        yaml.safe_dump(active_config.model_dump(mode="json"), sort_keys=False),
                        encoding="utf-8",
                    )
        elif res.status == "error":
            raise HTTPException(status_code=400, detail=res.message or "Verification failed")
        return res.model_dump()

    @router.post("/test")
    async def test(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = payload.get("endpoint") if payload else None
        return driver.test_connection(endpoint).model_dump()

    return router
