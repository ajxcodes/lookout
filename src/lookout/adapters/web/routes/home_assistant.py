"""REST API endpoints for Home Assistant integration management."""

import inspect
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from lookout.config.schema import HomeAssistantConfig, mask_config_dict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["home-assistant"])


def _mask_token_data(data: dict[str, Any]) -> dict[str, Any]:
    return mask_config_dict(data)


@router.get("/home-assistant/status")
async def ha_status(request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "ha_sync", None)
    status = {}
    if manager is not None and hasattr(manager, "get_status"):
        try:
            status = manager.get_status()
            if inspect.isawaitable(status):
                status = await status
        except Exception as exc:
            status = {"ok": False, "error": str(exc)}
    if not isinstance(status, dict):
        try:
            status = dict(status)
        except Exception:
            status = {"value": status}
    return {"status": status, "ok": status.get("ok", True) if isinstance(status, dict) else True}


@router.get("/home-assistant/config")
async def ha_get_config(request: Request) -> dict[str, Any]:
    config = getattr(request.app.state, "config", None)
    ha_cfg = HomeAssistantConfig()
    if config is not None and getattr(config, "home_assistant", None) is not None:
        ha_cfg = config.home_assistant
    data = ha_cfg.model_dump(mode="json")
    masked = _mask_token_data(data)
    # Return both flat and nested for compatibility
    return {**masked, "home_assistant": masked}


@router.put("/home-assistant/config")
async def ha_put_config(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=404, detail="Not configured")
    ha_cfg = getattr(config, "home_assistant", None)
    if ha_cfg is None:
        ha_cfg = HomeAssistantConfig()
        config.home_assistant = ha_cfg
    if not isinstance(body, dict):
        body = {}
    # Accept flat or nested
    payload = body.get("home_assistant", body) if isinstance(body, dict) else body
    if not isinstance(payload, dict):
        payload = {}
    for key, val in payload.items():
        if not hasattr(ha_cfg, key):
            continue
        # Avoid overwriting stored secrets with masked/empty submitted values.
        if key in ("access_token", "mqtt_password") and (
            val is None or (isinstance(val, str) and (not val.strip() or "*" in val))
        ):
            continue
        setattr(ha_cfg, key, val)
    # persist
    config_path = getattr(request.app.state, "config_path", None)
    if config_path:
        try:
            from lookout.providers.config_io import atomic_write_config

            atomic_write_config(config, config_path)
        except Exception as exc:
            logger.warning("Failed to persist HA config: %s", exc)
    # update manager
    manager = getattr(request.app.state, "ha_sync", None)
    if manager is not None:
        try:
            manager.base_url = str(getattr(ha_cfg, "base_url", None) or manager.base_url).rstrip(
                "/"
            )
            manager.access_token = getattr(ha_cfg, "access_token", None) or manager.access_token
            manager.mqtt_password = getattr(ha_cfg, "mqtt_password", None) or manager.mqtt_password
            manager.ingress_url = str(
                getattr(ha_cfg, "ingress_url", None) or manager.ingress_url
            ).rstrip("/")
        except Exception:
            pass
    data = ha_cfg.model_dump(mode="json")
    masked = _mask_token_data(data)
    return {**masked, "home_assistant": masked, "status": "ok"}


@router.post("/home-assistant/test")
async def ha_test(request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "ha_sync", None)
    if manager is None:
        return {"ok": False, "error": "sync manager unavailable"}
    try:
        result = manager.test_connection()
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if not isinstance(result, dict):
        result = {"ok": bool(result), "result": result}
    if "ok" not in result:
        result["ok"] = True
    return result


@router.post("/home-assistant/sync")
async def ha_sync_now(request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "ha_sync", None)
    if manager is None:
        return {"ok": False, "error": "sync manager unavailable"}
    try:
        result = manager.sync_all()
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if not isinstance(result, dict):
        result = {"ok": bool(result), "result": result}
    return result


@router.get("/home-assistant/cameras/{camera_id}")
async def ha_camera_status(camera_id: str, request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "ha_sync", None)
    status = {}
    if manager is not None and hasattr(manager, "get_status"):
        try:
            status = manager.get_status()
            if inspect.isawaitable(status):
                status = await status
        except Exception as exc:
            status = {"ok": False, "error": str(exc)}
    if not isinstance(status, dict):
        try:
            status = dict(status)
        except Exception:
            status = {}
    cam_ts = (
        status.get("cameras", {}).get(camera_id)
        if isinstance(status.get("cameras"), dict)
        else None
    )
    topics = (
        status.get("published_topics", {}).get(camera_id)
        if isinstance(status.get("published_topics"), dict)
        else None
    )
    return {"camera_id": camera_id, "last_published": cam_ts, "topics": topics, "status": status}
