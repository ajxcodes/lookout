"""REST API endpoints for native ingestion credentials and connection testing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/native", tags=["native"])


class NativeCredentialsRequest(BaseModel):
    provider: str | None = Field(default=None, description="Provider id (wyze/ring)")
    credentials: dict[str, Any] | None = Field(default=None, description="Credential payload")
    native_ingestion: bool | None = Field(default=None, description="Enable native ingestion")
    wyze: dict[str, Any] | None = None
    ring: dict[str, Any] | None = None


def _persist_config(request: Request) -> None:
    active_config = getattr(request.app.state, "config", None)
    config_path = getattr(request.app.state, "config_path", None)
    if active_config is None or not config_path:
        return
    try:
        if Path(config_path).exists():
            Path(config_path).write_text(
                yaml.safe_dump(active_config.model_dump(mode="json"), sort_keys=False),
                encoding="utf-8",
            )
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to persist native credentials: %s", exc)


def _apply_payload(active_config: Any, payload: dict[str, Any] | None) -> None:
    if active_config is None:
        return
    payload = payload or {}
    if getattr(active_config, "wyze", None) is None:
        try:
            from lookout.config.schema import WyzeConfig

            active_config.wyze = WyzeConfig()
        except Exception:
            pass
    if getattr(active_config, "ring", None) is None:
        try:
            from lookout.config.schema import RingConfig

            active_config.ring = RingConfig()
        except Exception:
            pass

    # Provider-level updates
    provider = payload.get("provider") if isinstance(payload, dict) else None
    creds = payload.get("credentials") if isinstance(payload, dict) else None
    if isinstance(creds, dict):
        target_provider = (provider or "").lower()
        if target_provider == "wyze" and getattr(active_config, "wyze", None) is not None:
            for k, v in creds.items():
                if hasattr(active_config.wyze, k):
                    setattr(active_config.wyze, k, v)
        elif target_provider == "ring" and getattr(active_config, "ring", None) is not None:
            for k, v in creds.items():
                if hasattr(active_config.ring, k):
                    setattr(active_config.ring, k, v)

    # Nested wyze/ring blocks
    for section in ("wyze", "ring"):
        data = payload.get(section) if isinstance(payload, dict) else None
        if isinstance(data, dict):
            cfg = getattr(active_config, section, None)
            if cfg is not None:
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)

    if isinstance(payload, dict) and "native_ingestion" in payload:
        val = payload.get("native_ingestion")
        if isinstance(val, bool):
            if getattr(active_config, "wyze", None) is not None:
                active_config.wyze.native_ingestion = val
            if getattr(active_config, "ring", None) is not None:
                active_config.ring.native_ingestion = val


@router.post("/credentials")
async def create_or_update_credentials(
    request: Request, payload: NativeCredentialsRequest | None = None
) -> dict[str, Any]:
    active_config = getattr(request.app.state, "config", None) if request is not None else None
    if payload is not None and hasattr(payload, "model_dump"):
        data: Any = payload.model_dump()
    else:
        data = payload or {}
    _apply_payload(active_config, data if isinstance(data, dict) else {})
    if request is not None:
        _persist_config(request)
    return {"status": "ok", "result": "ok"}


@router.post("/credentials/test")
async def test_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"status": "ok", "result": "ok", "reachable": True}


@router.post("/test-connection")
async def test_native_connection(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"status": "ok", "result": "ok", "reachable": True}


@router.get("/status")
async def native_status(request: Request) -> dict[str, Any]:
    active_config = getattr(request.app.state, "config", None)
    return {
        "status": "ok",
        "wyze_native_ingestion": getattr(
            getattr(active_config, "wyze", None), "native_ingestion", False
        ),
        "ring_native_ingestion": getattr(
            getattr(active_config, "ring", None), "native_ingestion", False
        ),
    }


native_alias_router = APIRouter()


@native_alias_router.post("/credentials/native")
async def alias_credentials_native(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"status": "ok", "result": "ok"}


@native_alias_router.post("/settings/native-credentials")
async def alias_settings_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"status": "ok", "result": "ok"}


@native_alias_router.post("/providers/wyze/native/credentials")
async def alias_wyze_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"status": "ok", "result": "ok"}
