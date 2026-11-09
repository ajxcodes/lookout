"""REST API endpoints for camera discovery, snapshot serving, and WebRTC metadata."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from lookout.adapters.web.helpers import build_camera_card_data, infer_camera_icon

__all__ = ["TINY_JPEG", "build_camera_card_data", "infer_camera_icon", "router"]

router = APIRouter(tags=["cameras"])

# Minimal valid 1x1 pixel JPEG binary for placeholder/idle snapshot delivery
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
    b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
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
            raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")

        # 1. Check for cached motion snapshot on disk
        base_path = Path(config.storage.base_path)
        snapshot_file = base_path / "snapshots" / f"{camera_id}.jpg"
        if snapshot_file.exists():
            return FileResponse(snapshot_file, media_type="image/jpeg")

        # 2. Dynamic live frame from go2rtc
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
                if resp.status_code == 200 and len(resp.content) > 150:
                    return Response(
                        content=resp.content,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                    )
        except Exception:
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
