"""REST API endpoints for camera discovery, snapshot serving, and WebRTC metadata."""

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lookout.adapters.web.helpers import build_camera_card_data, infer_camera_icon

__all__ = ["TINY_JPEG", "build_camera_card_data", "infer_camera_icon", "router"]

router = APIRouter(tags=["cameras"])

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

    cameras = [build_camera_card_data(cam) for cam in config.cameras]
    return {"cameras": cameras}


@router.get("/cameras/{camera_id}/snapshot")
async def get_camera_snapshot(camera_id: str, request: Request) -> Response:
    """Serve the latest snapshot image for a camera feed."""
    config = request.app.state.config
    if config is not None and hasattr(config, "cameras"):
        cam_ids = {c.id for c in config.cameras}
        if camera_id not in cam_ids:
            raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found") from None

        # 1. Check for cached motion snapshot on disk
        base_path = Path(config.storage.base_path)
        snapshot_file = base_path / "snapshots" / f"{camera_id}.jpg"
        if snapshot_file.exists():
            return FileResponse(snapshot_file, media_type="image/jpeg")

        cam_obj = next((c for c in config.cameras if c.id == camera_id), None)
        card_data = build_camera_card_data(cam_obj) if cam_obj is not None else {}
        is_on_demand = card_data.get("stream_mode") == "on_demand"

        # 2. Check for latest recorded event snapshot on disk before hitting live stream
        event_snap_dirs = [
            base_path / "snapshots" / "events",
            Path("/snapshots/events"),
            Path("/tmp/lookout_snapshots"),
        ]
        for snap_dir in event_snap_dirs:
            if snap_dir.is_dir():
                matches = sorted(snap_dir.glob(f"{camera_id}_*.jpg"))
                if matches:
                    return FileResponse(matches[-1], media_type="image/jpeg")

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
    notifications_muted: bool | None = None
    stream_mode: str | None = None


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
    _require_api_key(request)
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
    return {
        "status": "ok",
        "enabled": getattr(cam, "enabled", None),
        "camera": {"id": camera_id, "enabled": getattr(cam, "enabled", None)},
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
