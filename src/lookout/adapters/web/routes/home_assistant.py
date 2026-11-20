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


def _is_real_secret(value: Any) -> bool:
    """Return True only for a concrete secret worth persisting/assigning.

    The config GET endpoint echoes secrets back masked, so a client round-trip
    can submit placeholders (``None``, blank strings, asterisks or the literal
    ``"masked"``). Those must never clobber a credential already stored on the
    manager.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(stripped) and "*" not in stripped and stripped.lower() != "masked"


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
    mode = status.get("mode") if isinstance(status, dict) else None
    if mode is None:
        manager = getattr(request.app.state, "ha_sync", None)
        detected = getattr(manager, "mode", None) if manager is not None else None
        mode = detected if isinstance(detected, str) else None
    response: dict[str, Any] = {
        "status": status,
        "ok": status.get("ok", True) if isinstance(status, dict) else True,
    }
    if mode is not None:
        response["mode"] = mode
    return response


@router.get("/home-assistant/config")
async def ha_get_config(request: Request) -> dict[str, Any]:
    config = getattr(request.app.state, "config", None)
    ha_cfg = HomeAssistantConfig()
    if config is not None and getattr(config, "home_assistant", None) is not None:
        ha_cfg = config.home_assistant
    data = ha_cfg.model_dump(mode="json")
    manager = getattr(request.app.state, "ha_sync", None)
    if manager is not None:
        # Return the effective (auto-detected) connection values so the UI can
        # prefill them without the user re-entering config that already exists.
        for attr in ("base_url", "ingress_url", "mqtt_host", "mqtt_port", "mqtt_username"):
            value = getattr(manager, attr, None)
            if value is not None:
                data[attr] = value
        detected_mode = getattr(manager, "mode", None)
        if isinstance(detected_mode, str):
            data["mode"] = detected_mode
    # Tokens / passwords stay masked (and are never taken from the manager,
    # which may hold a credential inherited from another config block).
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
        if key in ("access_token", "mqtt_password") and not _is_real_secret(val):
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
            # Only a real submitted secret may replace the one the manager
            # already holds; masked/placeholder values must be ignored.
            token = getattr(ha_cfg, "access_token", None)
            if _is_real_secret(token):
                manager.access_token = token
            manager.mqtt_host = getattr(ha_cfg, "mqtt_host", None) or manager.mqtt_host
            manager.mqtt_port = getattr(ha_cfg, "mqtt_port", None) or manager.mqtt_port
            manager.mqtt_username = getattr(ha_cfg, "mqtt_username", None) or manager.mqtt_username
            mqtt_password = getattr(ha_cfg, "mqtt_password", None)
            if _is_real_secret(mqtt_password):
                manager.mqtt_password = mqtt_password
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
