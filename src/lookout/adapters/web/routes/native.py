"""REST API endpoints for native ingestion credentials, discovery, and status."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from lookout.config.schema import RingConfig, WyzeConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/native", tags=["native"])


class NativeCredentialsRequest(BaseModel):
    provider: str | None = Field(default=None, description="Provider id (wyze/ring)")
    credentials: dict[str, Any] | None = Field(default=None, description="Credential payload")
    native_ingestion: bool | None = Field(default=None, description="Enable native ingestion")
    wyze: dict[str, Any] | None = None
    ring: dict[str, Any] | None = None


def _persist_config(request: Request) -> None:
    """Persist the active config, creating parent directories as needed."""
    active_config = getattr(request.app.state, "config", None)
    config_path = getattr(request.app.state, "config_path", None)
    if active_config is None or not config_path:
        return
    path = Path(config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(active_config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        # The file carries cloud refresh tokens, so keep it owner-only.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning("Failed to persist native credentials to %s: %s", path, exc)


def _merge_validated(config: Any, updates: dict[str, Any], model: type[Any]) -> Any:
    """Validate a merge of ``updates`` into ``config`` without mutating it.

    Returns a fully validated model instance. Callers are responsible for copying
    the validated fields onto the original object when identity must be preserved
    (see :func:`_update_in_place`).
    """
    merged = {**config.model_dump(), **updates}
    return model.model_validate(merged)


def _update_in_place(existing: Any, updates: dict[str, Any], model: type[Any]) -> Any:
    """Validate merged values, then copy them onto the live config object.

    Native adapters (``app.state.wyze_native`` / ``app.state.ring_native``) hold a
    reference to these config objects, so replacing the object would leave them
    running with stale credentials. Copying field-by-field preserves identity.
    """
    if existing is None:
        return model.model_validate(updates)
    validated = _merge_validated(existing, updates, model)
    for name in type(existing).model_fields:
        setattr(existing, name, getattr(validated, name))
    return existing


def _apply_payload(active_config: Any, payload: dict[str, Any] | None) -> None:
    """Apply credential/nested-block updates through strict model validation."""
    if active_config is None or not isinstance(payload, dict):
        return

    provider = payload.get("provider")
    credentials = payload.get("credentials")
    if isinstance(credentials, dict) and credentials:
        target = str(provider or "").lower()
        if target == "wyze":
            active_config.wyze = _update_in_place(active_config.wyze, credentials, WyzeConfig)
        elif target == "ring":
            active_config.ring = _update_in_place(active_config.ring, credentials, RingConfig)

    for section, model in (("wyze", WyzeConfig), ("ring", RingConfig)):
        block = payload.get(section)
        if isinstance(block, dict) and block:
            setattr(
                active_config,
                section,
                _update_in_place(getattr(active_config, section), block, model),
            )

    native_ingestion = payload.get("native_ingestion")
    if isinstance(native_ingestion, bool):
        active_config.wyze.native_ingestion = native_ingestion
        active_config.ring.native_ingestion = native_ingestion


def _coerce_request(payload: Any) -> NativeCredentialsRequest | None:
    if payload is None:
        return None
    if isinstance(payload, NativeCredentialsRequest):
        return payload
    if isinstance(payload, dict):
        return NativeCredentialsRequest(**payload)
    return None


def _build_adapter(request: Request, provider: str, credentials: dict[str, Any] | None) -> Any:
    """Build a temporary adapter from supplied credentials."""
    from lookout.adapters.ring_native import RingNativeAdapter
    from lookout.adapters.wyze_native import WyzeNativeAdapter

    state = request.app.state
    manager = getattr(state, "native_stream_manager", None)
    active_config = getattr(state, "config", None)
    if provider == "wyze":
        base = getattr(active_config, "wyze", None) or WyzeConfig()
        config = _merge_validated(base, credentials, WyzeConfig) if credentials else base
        return WyzeNativeAdapter(
            config=config,
            stream_manager=manager,
            cameras=(getattr(active_config, "cameras", None) or []),
        )
    if provider == "ring":
        base = getattr(active_config, "ring", None) or RingConfig()
        config = _merge_validated(base, credentials, RingConfig) if credentials else base
        return RingNativeAdapter(
            config=config,
            stream_manager=manager,
            cameras=(getattr(active_config, "cameras", None) or []),
        )
    raise HTTPException(status_code=400, detail=f"Unknown native provider '{provider}'")


def _resolve_adapter(request: Request, provider: str, credentials: dict[str, Any] | None) -> Any:
    """Use the app-state adapter when possible, else build a temporary one."""
    if not credentials:
        adapter = getattr(request.app.state, f"{provider}_native", None)
        if adapter is not None:
            return adapter
    return _build_adapter(request, provider, credentials)


def _normalize_result(provider: str, result: dict[str, Any]) -> dict[str, Any]:
    reachable = bool(result.get("reachable"))
    return {
        "status": "ok" if reachable else "error",
        "reachable": reachable,
        "provider": provider,
        "devices": list(result.get("devices") or []),
        "error": result.get("error"),
    }


@router.post("/credentials")
async def create_or_update_credentials(
    request: Request, payload: NativeCredentialsRequest | None = None
) -> dict[str, Any]:
    active_config = getattr(request.app.state, "config", None)
    data: dict[str, Any] = payload.model_dump() if payload is not None else {}
    try:
        _apply_payload(active_config, data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    _persist_config(request)
    return {"status": "ok", "result": "ok"}


async def _test_provider(
    request: Request, provider: str, credentials: dict[str, Any] | None
) -> dict[str, Any]:
    try:
        adapter = _resolve_adapter(request, provider, credentials)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    try:
        result = await adapter.test_connection()
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        return {
            "status": "error",
            "reachable": False,
            "provider": provider,
            "devices": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return _normalize_result(provider, result)


@router.post("/credentials/test")
async def test_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = payload or {}
    provider = data.get("provider")
    credentials = data.get("credentials")
    if not provider:
        return {
            "wyze": await _test_provider(request, "wyze", credentials),
            "ring": await _test_provider(request, "ring", credentials),
        }
    return await _test_provider(request, str(provider).lower(), credentials)


@router.post("/test-connection")
async def test_native_connection(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await test_native_credentials(request, payload)


@router.post("/discover")
async def native_discover(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = payload or {}
    provider = str(data.get("provider") or "").lower()
    if provider not in ("wyze", "ring"):
        raise HTTPException(status_code=400, detail="provider must be 'wyze' or 'ring'")
    credentials = data.get("credentials")
    try:
        adapter = _resolve_adapter(request, provider, credentials)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    try:
        devices = await adapter.discover_devices()
    except Exception as exc:  # noqa: BLE001 - surfaced as a provider error
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return {"status": "ok", "provider": provider, "devices": devices}


@router.get("/status")
async def native_status(request: Request) -> dict[str, Any]:
    active_config = getattr(request.app.state, "config", None)
    manager = getattr(request.app.state, "native_stream_manager", None)
    wyze_cfg = getattr(active_config, "wyze", None)
    ring_cfg = getattr(active_config, "ring", None)
    stream_manager: dict[str, Any] | None = None
    if manager is not None:
        stream_manager = {
            "running": bool(getattr(manager, "running", False)),
            "api_url": getattr(manager, "api_url", None),
            "streams": dict(getattr(manager, "streams", {}) or {}),
            "binary": getattr(manager, "binary_path", None),
        }
    poller = getattr(request.app.state, "native_event_poller", None)
    return {
        "status": "ok",
        "wyze_native_ingestion": bool(getattr(wyze_cfg, "native_ingestion", False)),
        "ring_native_ingestion": bool(getattr(ring_cfg, "native_ingestion", False)),
        "wyze_configured": bool(
            getattr(wyze_cfg, "auth_token", None)
            or getattr(wyze_cfg, "refresh_token", None)
            or getattr(wyze_cfg, "email", None)
        ),
        "ring_configured": bool(
            getattr(ring_cfg, "refresh_token", None) or getattr(ring_cfg, "auth_token", None)
        ),
        "stream_manager": stream_manager,
        "event_poller_running": bool(getattr(poller, "running", False)),
    }


native_alias_router = APIRouter()


@native_alias_router.post("/credentials/native")
async def alias_credentials_native(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await create_or_update_credentials(request, _coerce_request(payload))


@native_alias_router.post("/settings/native-credentials")
async def alias_settings_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await create_or_update_credentials(request, _coerce_request(payload))


@native_alias_router.post("/providers/wyze/native/credentials")
async def alias_wyze_native_credentials(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = dict(payload or {})
    data.setdefault("provider", "wyze")
    return await create_or_update_credentials(request, _coerce_request(data))
