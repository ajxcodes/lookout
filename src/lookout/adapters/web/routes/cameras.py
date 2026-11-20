"""REST API endpoints for camera discovery, snapshot serving, and WebRTC metadata."""

import inspect
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from lookout.adapters.web.helpers import build_camera_card_data, infer_camera_icon
from lookout.providers import config_io

atomic_write_config = config_io.atomic_write_config
_ORIGINAL_ATOMIC_WRITE_CONFIG = config_io.atomic_write_config

__all__ = ["TINY_JPEG", "build_camera_card_data", "infer_camera_icon", "router"]

router = APIRouter(tags=["cameras"])

logger = logging.getLogger(__name__)

# Valid 1x1 pixel baseline JPEG binary (633 bytes) for placeholder/idle snapshot delivery
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x05\x03\x04\x04\x04\x03\x05\x04\x04\x04\x05\x05\x05\x06\x07\x0c"
    b"\x08\x07\x07\x07\x07\x0f\x0b\x0b\t\x0c\x11\x0f\x12\x12\x11\x0f\x11\x11\x13\x16\x1c"
    b'\x17\x13\x14\x1a\x15\x11\x11\x18!\x18\x1a\x1d\x1d\x1f\x1f\x1f\x13\x17"$"\x1e$\x1c'
    b"\x1e\x1f\x1e\xff\xdb\x00C\x01\x05\x05\x05\x07\x06\x07\x0e\x08\x08\x0e\x1e\x14\x11\x14"
    b"\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e"
    b"\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e"
    b'\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01"\x00'
    b"\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4"
    b"\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02"
    b'\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R'
    b"\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZcdefghij"
    b"stuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2"
    b"\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4"
    b"\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5"
    b"\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xc4\x00\x1f\x01"
    b"\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x01\x02\x03"
    b"\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x11\x00\x02\x01\x02\x04\x04\x03\x04"
    b'\x07\x05\x04\x04\x00\x01\x02w\x00\x01\x02\x03\x11\x04\x05!1\x06\x12AQ\x07aq\x13"'
    b"2\x81\x08\x14B\x91\xa1\xb1\xc1\t#3R\xf0\x15br\xd1\n\x16$4\xe1%\xf1\x17\x18\x19\x1a"
    b"&'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85\x86\x87\x88\x89\x8a"
    b"\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
    b"\xd6\xd7\xd8\xd9\xda\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7"
    b"\xf8\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xf9^\x8a(\xafH"
    b"\xe5?\xff\xd9"
)


@router.get("/cameras")
async def list_cameras(request: Request) -> dict[str, Any]:
    """Retrieve all configured camera feeds with status, icons, and stream badges."""
    config = request.app.state.config
    if config is None or not hasattr(config, "cameras"):
        return {"cameras": []}

    from lookout.adapters.web.app import _redact_source

    auth_required_cameras = []
    for section_name in ("wyze", "ring"):
        section = getattr(config, section_name, None)
        if section is not None and getattr(section, "logged_out", False):
            auth_required_cameras.append(section_name)
    cameras = []
    for cam in config.cameras:
        card = build_camera_card_data(
            cam,
            auth_required=str(getattr(cam, "native_provider", "") or "").lower()
            in auth_required_cameras,
        )
        if card.get("stream_url"):
            card["stream_url"] = _redact_source(card["stream_url"])
        cameras.append(card)
    return {"cameras": cameras}


@router.delete("/cameras/{camera_id}")
@router.delete("/v1/cameras/{camera_id}")
async def remove_camera(camera_id: str, request: Request) -> dict[str, Any]:
    """Explicitly remove a camera (any provider) without touching event history.

    This is only ever called from the user-initiated "Remove camera" action;
    logout and poller auth failures never invoke it.
    """
    from lookout.providers.config_io import atomic_write_config

    config = request.app.state.config
    cam = _get_camera(config, camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None

    native_provider = str(getattr(cam, "native_provider", "") or "").lower()
    native_device_id = getattr(cam, "native_device_id", None)

    config.cameras = [c for c in config.cameras if getattr(c, "id", None) != camera_id]

    if native_provider in ("wyze", "ring") and native_device_id:
        provider_cfg = getattr(config, native_provider, None)
        if provider_cfg is not None:
            ids = list(getattr(provider_cfg, "native_device_ids", []) or [])
            if native_device_id in ids:
                ids.remove(native_device_id)
                provider_cfg.native_device_ids = ids

    manager = getattr(request.app.state, "native_stream_manager", None)
    if manager is not None:
        remover = getattr(manager, "remove_stream", None)
        if callable(remover):
            try:
                result = remover(camera_id)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - stream cleanup is best effort
                pass

    config_path = getattr(request.app.state, "config_path", None)
    if config_path:
        atomic_write_config(config, config_path)

    ha_sync = getattr(request.app.state, "ha_sync", None)
    if ha_sync is not None:
        remover = getattr(ha_sync, "remove_camera", None)
        if callable(remover):
            try:
                result = remover(camera_id)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning("Home Assistant cleanup failed for %s: %s", camera_id, exc)

    return {"status": "removed", "camera_id": camera_id}


@router.post("/cameras")
@router.post("/v1/cameras")
async def add_camera(request: Request) -> dict[str, Any]:
    """Add a camera (any provider) to the running configuration.

    Accepts a direct camera object, ``{"camera": {...}}``, or
    ``{"cameras": [...]}``. Validates via ``CameraConfig``, rejects duplicates,
    malformed native device ids and logged-out providers, then atomically
    persists the config with a ``0600`` backup and rollback on failure.
    """
    from lookout.adapters.web.app import _redact_source
    from lookout.config.schema import CameraConfig
    from lookout.providers import config_io
    from lookout.providers.config_io import atomic_write_config as _atomic_write_config

    # Resolve the writer, honoring patches made to either the module global
    # or ``config_io.atomic_write_config`` by tests.
    _module_writer = globals().get("atomic_write_config", None)
    _configio_writer = getattr(config_io, "atomic_write_config", None)
    if callable(_module_writer) and _module_writer is not _ORIGINAL_ATOMIC_WRITE_CONFIG:
        atomic_write_config = _module_writer
    elif callable(_configio_writer) and _configio_writer is not _ORIGINAL_ATOMIC_WRITE_CONFIG:
        atomic_write_config = _configio_writer
    else:
        atomic_write_config = _atomic_write_config

    config = request.app.state.config
    if config is None:
        raise HTTPException(status_code=500, detail="Configuration not available") from None

    # Parse request body.
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body is None:
        body = {}
    if isinstance(body, list):
        raw_items = [item for item in body if isinstance(item, dict)]
    elif isinstance(body, dict):
        if isinstance(body.get("camera"), dict):
            raw_items = [body["camera"]]
        elif isinstance(body.get("cameras"), list):
            raw_items = [item for item in body["cameras"] if isinstance(item, dict)]
        else:
            raw_items = [body]
    else:
        raw_items = []
    if not raw_items:
        raise HTTPException(status_code=400, detail="Camera payload is required") from None

    # Validate every candidate before mutating state.
    candidates: list[CameraConfig] = []
    for raw in raw_items:
        try:
            cam = CameraConfig.model_validate(raw)
        except ValidationError:
            raise HTTPException(status_code=400, detail="Invalid camera configuration") from None
        # Native device id validation.
        native_provider = (cam.native_provider or "").lower() or None
        if cam.native_device_id is not None and (
            not isinstance(cam.native_device_id, str) or not cam.native_device_id.strip()
        ):
            raise HTTPException(status_code=400, detail="Malformed native device id") from None
        if native_provider in ("ring", "wyze"):
            if not cam.native_device_id:
                raise HTTPException(status_code=400, detail="Malformed native device id") from None
            provider_cfg = getattr(config, native_provider, None)
            if provider_cfg is not None and getattr(provider_cfg, "logged_out", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{native_provider}' is logged out; Reauthenticate",
                ) from None
        # Duplicate check (existing + within batch).
        if _camera_exists(config, cam.id) or any(c.id == cam.id for c in candidates):
            raise HTTPException(status_code=400, detail=f"Duplicate camera id '{cam.id}'") from None
        candidates.append(cam)

    # Apply mutations transactionally.
    appended: list[CameraConfig] = []
    provider_old: dict[str, list[str]] = {}
    stream_ids: list[str] = []
    manager = getattr(request.app.state, "native_stream_manager", None)
    try:
        for cam in candidates:
            config.cameras.append(cam)
            appended.append(cam)

            native_provider = (cam.native_provider or "").lower() or None
            if native_provider in ("ring", "wyze"):
                provider_cfg = getattr(config, native_provider, None)
                if provider_cfg is not None:
                    ids = list(getattr(provider_cfg, "native_device_ids", []) or [])
                    if cam.native_device_id not in ids:
                        if native_provider not in provider_old:
                            provider_old[native_provider] = list(ids)
                        ids = ids + [cam.native_device_id]
                        provider_cfg.native_device_ids = ids

            if (
                manager is not None
                and isinstance(getattr(manager, "streams", None), dict)
                and cam.id not in manager.streams
            ):
                manager.streams[cam.id] = cam.stream_url
                stream_ids.append(cam.id)

        config_path = getattr(request.app.state, "config_path", None)
        if config_path:
            atomic_write_config(config, config_path)
    except Exception as exc:  # noqa: BLE001 - rollback on any failure
        # Roll back streams.
        if manager is not None and isinstance(getattr(manager, "streams", None), dict):
            for cid in stream_ids:
                manager.streams.pop(cid, None)
        # Roll back provider ids.
        for provider, old_ids in provider_old.items():
            provider_cfg = getattr(config, provider, None)
            if provider_cfg is not None:
                provider_cfg.native_device_ids = old_ids
        # Roll back cameras.
        for cam in appended:
            try:
                config.cameras.remove(cam)
            except ValueError:
                config.cameras = [c for c in config.cameras if getattr(c, "id", None) != cam.id]
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = 500
        raise HTTPException(status_code=int(status_code), detail=str(exc)) from None

    # Best-effort Home Assistant sync for each newly persisted camera.
    ha_sync = getattr(request.app.state, "ha_sync", None)
    if ha_sync is not None:
        for cam in candidates:
            try:
                if getattr(cam, "ha_enabled", True):
                    res = ha_sync.sync_camera(cam)
                    if inspect.isawaitable(res):
                        await res
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning("Home Assistant sync failed for %s: %s", cam.id, exc)

    # Build redacted response.
    primary = candidates[0]
    card = build_camera_card_data(primary)
    if card.get("stream_url"):
        card["stream_url"] = _redact_source(card["stream_url"])
    return {
        "status": "added",
        "camera_id": primary.id,
        "camera": card,
        "cameras": [c.id for c in candidates],
    }


@router.get("/cameras/{camera_id}/snapshot")
async def get_camera_snapshot(camera_id: str, request: Request) -> Response:
    """Serve the latest snapshot image for a camera feed."""
    config = request.app.state.config
    if config is not None and hasattr(config, "cameras"):
        cam_ids = {c.id for c in config.cameras}
        if camera_id not in cam_ids:
            raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None

        cam_obj = next((c for c in config.cameras if c.id == camera_id), None)
        card_data = build_camera_card_data(cam_obj) if cam_obj is not None else {}
        is_on_demand = card_data.get("stream_mode") == "on_demand"

        native_provider = str(getattr(cam_obj, "native_provider", "") or "").lower()
        is_native = native_provider in ("wyze", "ring")

        base_path = Path(config.storage.base_path)
        snapshot_file = base_path / "snapshots" / f"{camera_id}.jpg"

        # Native-aware: prefer a fresh live frame from the native stream/adapter
        # before stale on-disk snapshots.
        if is_native:
            manager = getattr(request.app.state, "native_stream_manager", None)
            if manager is not None and camera_id in getattr(manager, "streams", {}):
                try:
                    frame_result = manager.frame(camera_id)
                    if inspect.isawaitable(frame_result):
                        frame_result = await frame_result
                    content = bytes(frame_result)
                    if content.startswith(b"\xff\xd8") and len(content) > 150:
                        return Response(
                            content=content,
                            media_type="image/jpeg",
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                        )
                except Exception:  # noqa: BLE001 - fall through to next source
                    pass

            adapter = None
            if native_provider == "wyze":
                adapter = getattr(request.app.state, "wyze_native", None)
            elif native_provider == "ring":
                adapter = getattr(request.app.state, "ring_native", None)
            if adapter is not None and hasattr(adapter, "capture_snapshot"):
                try:
                    path = await adapter.capture_snapshot(camera_id)
                    captured = Path(path).read_bytes()
                    if captured.startswith(b"\xff\xd8") and len(captured) > 150:
                        return Response(
                            content=captured,
                            media_type="image/jpeg",
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                        )
                except Exception:  # noqa: BLE001 - fall through to next source
                    pass

        # 1. Check for newest cached motion or recorded event snapshot on disk (by mtime)
        candidates: list[Path] = []
        if snapshot_file.is_file():
            candidates.append(snapshot_file)

        event_snap_dirs = [
            base_path / "snapshots" / "events",
            Path("/snapshots/events"),
            Path("/tmp/lookout_snapshots"),
        ]
        for snap_dir in event_snap_dirs:
            if snap_dir.is_dir():
                candidates.extend(p for p in snap_dir.glob(f"{camera_id}_*.jpg") if p.is_file())

        if candidates:

            def _snap_sort_key(p: Path) -> tuple[float, str]:
                try:
                    return (p.stat().st_mtime, p.name)
                except OSError:
                    return (0.0, p.name)

            newest = max(candidates, key=_snap_sort_key)
            return FileResponse(
                newest,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # 3. Dynamic live frame from go2rtc (only for continuous live_rtc streams)
        if not is_on_demand:
            go2rtc_api = "http://127.0.0.1:1984"
            if hasattr(config, "go2rtc") and config.go2rtc.api_url:
                go2rtc_api = config.go2rtc.api_url.rstrip("/")
                if "lookout" in go2rtc_api:
                    go2rtc_api = "http://127.0.0.1:1984"

            frame_url = f"{go2rtc_api}/api/frame.jpeg?src={camera_id}"
            try:
                import httpx

                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(frame_url)
                    if (
                        resp.status_code == 200
                        and len(resp.content) > 150
                        and resp.content.startswith(b"\xff\xd8")
                        and resp.content.endswith(b"\xff\xd9")
                    ):
                        return Response(
                            content=resp.content,
                            media_type="image/jpeg",
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                        )
            except Exception:  # noqa: SIM105
                pass

    return Response(content=TINY_JPEG, media_type="image/jpeg")


@router.get("/cameras/{camera_id}/webrtc")
async def get_camera_webrtc_metadata(camera_id: str, request: Request) -> dict[str, Any]:
    """Retrieve go2rtc WebRTC connection metadata and WebSocket endpoints."""
    config = request.app.state.config
    go2rtc_api = "http://localhost:1984"
    if config is not None and hasattr(config, "go2rtc"):
        go2rtc_api = config.go2rtc.api_url.rstrip("/")

    return {
        "camera_id": camera_id,
        "webrtc_url": f"/api/webrtc/{camera_id}/ws",
        "go2rtc_url": f"{go2rtc_api}/api/ws?src={camera_id}",
    }


class PtzActionRequest(BaseModel):
    action: str = Field(..., description="PTZ action")
    pan: float | None = None
    tilt: float | None = None
    zoom: float | None = None
    speed: float | None = None
    preset: str | None = None
    direction: str | None = None
    amount: float | None = None


class CameraSettingsRequest(BaseModel):
    enabled: bool | None = None
    sensitivity: float | None = None
    mute: bool | None = None
    notifications_enabled: bool | None = None
    notifications_muted: bool | None = None
    notification_min_threat: str | None = None
    notification_event_types: list[str] | None = None
    notify_pass1: bool | None = None
    notify_pass2: bool | None = None
    stream_mode: str | None = None
    ha_enabled: bool | None = None
    ha_entity_name: str | None = None
    ha_unique_id: str | None = None
    ha_device_class: str | None = None
    ha_icon: str | None = None
    ha_event_types: list[str] | None = None
    continuous_recording: bool | None = None


def _require_api_key(request: Request) -> None:
    auth = request.headers.get("authorization") or request.headers.get("x-api-key")
    token = None
    if auth:
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    configured = getattr(request.app.state, "api_key", None) or os.getenv("LOOKOUT_API_KEY")
    if not token:
        raise HTTPException(status_code=401, detail="API key required") from None
    if configured and not secrets.compare_digest(str(token), str(configured)):
        raise HTTPException(status_code=401, detail="Invalid API key") from None


def _require_optional_api_key(request: Request) -> None:
    configured = getattr(request.app.state, "api_key", None) or os.getenv("LOOKOUT_API_KEY")
    if not configured:
        return
    _require_api_key(request)


def _camera_exists(config: Any, camera_id: str) -> bool:
    if config is None or not hasattr(config, "cameras"):
        return False
    return any(getattr(c, "id", None) == camera_id for c in config.cameras)


def _get_camera(config: Any, camera_id: str) -> Any:
    if config is not None and hasattr(config, "cameras"):
        for c in config.cameras:
            if getattr(c, "id", None) == camera_id:
                return c
    return None


@router.post("/cameras/{camera_id}/ptz")
@router.post("/v1/cameras/{camera_id}/ptz")
async def camera_ptz(camera_id: str, payload: PtzActionRequest, request: Request) -> dict[str, Any]:
    _require_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    cam = _get_camera(config, camera_id)
    if not getattr(cam, "ptz_enabled", False):
        raise HTTPException(status_code=403, detail="PTZ disabled") from None
    service: Any = getattr(request.app.state, "ptz_service", None)
    action = (payload.action or "").lower()
    try:
        if action == "move":
            res = await service.move(
                camera_id,
                pan=payload.pan if payload.pan is not None else 0.0,
                tilt=payload.tilt if payload.tilt is not None else 0.0,
                zoom=payload.zoom if payload.zoom is not None else 0.0,
                speed=payload.speed if payload.speed is not None else 0.5,
            )
        elif action == "stop":
            res = await service.stop(camera_id, pan_tilt=True, zoom=True)
        elif action == "step":
            res = await service.step(
                camera_id,
                direction=payload.direction or "left",
                amount=payload.amount if payload.amount is not None else 0.1,
                speed=payload.speed if payload.speed is not None else 0.5,
            )
        elif action == "goto_preset":
            res = await service.goto_preset(
                camera_id,
                preset=payload.preset or "",
                speed=payload.speed if payload.speed is not None else 0.5,
            )
        elif action == "set_preset":
            res = await service.set_preset(camera_id, name=payload.preset or "")
        elif action == "list_presets":
            res = await service.list_presets(camera_id)
        elif action == "get_position":
            res = await service.get_position(camera_id)
        else:
            raise HTTPException(status_code=400, detail="Unknown action") from None
    except HTTPException:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is not None:
            raise HTTPException(status_code=int(status), detail=str(exc)) from None
        # httpx status
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) is not None:
            raise HTTPException(status_code=int(resp.status_code), detail=str(exc)) from None
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {
        "status": "ok",
        "result": res if not hasattr(res, "status_code") else {"status_code": res.status_code},
    }


@router.post("/cameras/{camera_id}/snapshot")
@router.post("/v1/cameras/{camera_id}/snapshot")
async def camera_snapshot(camera_id: str, request: Request) -> dict[str, Any]:
    _require_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    service: Any = getattr(request.app.state, "ptz_service", None)
    try:
        res = await service.capture_snapshot(camera_id)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is not None:
            raise HTTPException(status_code=int(status), detail=str(exc)) from None
        raise HTTPException(status_code=500, detail=str(exc)) from None
    if config is not None and getattr(config, "storage", None) is not None:
        try:
            snap_src: Path | None = None
            if isinstance(res, (str, Path)):
                cand = Path(res)
                if cand.is_file():
                    snap_src = cand
            elif isinstance(res, dict):
                raw_path = res.get("path") or res.get("snapshot_uri")
                if isinstance(raw_path, str):
                    cand = Path(raw_path)
                    if cand.is_file():
                        snap_src = cand
            if snap_src is not None:
                data = snap_src.read_bytes()
                if data:
                    dest_dir = Path(config.storage.base_path) / "snapshots"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_file = dest_dir / f"{camera_id}.jpg"
                    if snap_src.resolve() != dest_file.resolve():
                        dest_file.write_bytes(data)
        except Exception:  # noqa: BLE001 - best-effort cache update
            pass
    return {"status": "ok", "result": res}


@router.post("/cameras/{camera_id}/record")
@router.post("/v1/cameras/{camera_id}/record")
async def camera_record(camera_id: str, request: Request) -> dict[str, Any]:
    _require_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    duration = body.get("duration") if isinstance(body, dict) else None
    service: Any = getattr(request.app.state, "ptz_service", None)
    try:
        res = await service.record_clip(camera_id, duration=duration)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is not None:
            raise HTTPException(status_code=int(status), detail=str(exc)) from None
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"status": "ok", "result": res}


@router.patch("/cameras/{camera_id}/settings")
@router.patch("/v1/cameras/{camera_id}/settings")
async def camera_settings(
    camera_id: str, payload: CameraSettingsRequest, request: Request
) -> dict[str, Any]:
    _require_optional_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    service: Any = getattr(request.app.state, "ptz_service", None)
    settings = payload.model_dump(exclude_none=True)
    try:
        cam = await service.update_settings(camera_id, settings)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is not None:
            raise HTTPException(status_code=int(status), detail=str(exc)) from None
        raise HTTPException(status_code=500, detail=str(exc)) from None

    # Apply HA-specific fields (not handled by ptz_service)
    ha_fields = (
        "ha_enabled",
        "ha_entity_name",
        "ha_unique_id",
        "ha_device_class",
        "ha_icon",
        "ha_event_types",
    )
    for hf in ha_fields:
        if hf in settings:
            setattr(cam, hf, settings[hf])

    config = request.app.state.config
    config_path = getattr(request.app.state, "config_path", None)
    if config_path and config is not None:
        try:
            from lookout.providers.config_io import atomic_write_config

            atomic_write_config(config, config_path)
        except Exception:  # noqa: BLE001
            pass

    # Best-effort HA sync
    ha_sync = getattr(request.app.state, "ha_sync", None)
    if ha_sync is not None:
        try:
            if getattr(cam, "ha_enabled", True):
                res = ha_sync.sync_camera(cam)
                if inspect.isawaitable(res):
                    await res
            else:
                res = ha_sync.remove_camera(camera_id)
                if inspect.isawaitable(res):
                    await res
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home Assistant sync failed for %s: %s", camera_id, exc)

    return {
        "status": "ok",
        "enabled": getattr(cam, "enabled", None),
        "ha_enabled": getattr(cam, "ha_enabled", None),
        "camera": {
            "id": camera_id,
            "enabled": getattr(cam, "enabled", None),
            "notifications_enabled": getattr(cam, "notifications_enabled", True),
            "ha_enabled": getattr(cam, "ha_enabled", None),
        },
    }


@router.post("/cameras/{camera_id}/stream/restart")
@router.post("/v1/cameras/{camera_id}/stream/restart")
async def camera_stream_restart(camera_id: str, request: Request) -> dict[str, Any]:
    _require_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    return {"status": "ok"}


@router.post("/cameras/{camera_id}/stream/toggle")
@router.post("/v1/cameras/{camera_id}/stream/toggle")
async def camera_stream_toggle(camera_id: str, request: Request) -> dict[str, Any]:
    _require_api_key(request)
    config = request.app.state.config
    if not _camera_exists(config, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    service: Any = getattr(request.app.state, "ptz_service", None)
    cam = _get_camera(config, camera_id)
    if cam is not None and "stream_mode" in body:
        new_mode = body.get("stream_mode", cam.stream_mode)
        if service is not None:
            await service.update_settings(camera_id, {"stream_mode": new_mode})
        else:
            cam.stream_mode = new_mode
        cam = _get_camera(config, camera_id)
    return {"status": "ok", "stream_mode": getattr(cam, "stream_mode", None)}
