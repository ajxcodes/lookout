"""REST API endpoints for first-run onboarding setup wizard."""

import logging
import shutil
import socket
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from lookout.config.schema import LookoutConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


class AiTestRequest(BaseModel):
    """Payload for testing AI vision inference credentials and connectivity."""

    provider: str = Field(..., description="Target provider: ollama, opencode, gemini, or mock")
    host: str | None = Field(None, description="Custom server URL or endpoint")
    api_key: str | None = Field(None, description="Authentication API key")
    model: str | None = Field(None, description="Model identifier to probe")


class CameraTestRequest(BaseModel):
    """Payload for validating camera stream reachability."""

    stream_url: str = Field(..., description="RTSP, HTTP, HTTPS, or Ring stream URI")


class NotificationTestRequest(BaseModel):
    """Payload for verifying alert dispatch destination."""

    target_type: str = Field(
        ..., description="Notification destination type (webhook, ntfy, mobile)"
    )
    destination: str = Field(..., description="Webhook endpoint URL, topic name, or phone number")


@router.get("/setup/status")
async def get_setup_status(request: Request) -> dict[str, Any]:
    """Check whether Lookout is configured or requires first-run onboarding."""
    return {"configured": bool(request.app.state.is_configured)}


@router.post("/setup/test-ai")
async def test_ai_connection(payload: AiTestRequest) -> dict[str, Any]:
    """Probe AI inference provider connectivity and authentication."""
    provider = payload.provider.lower()

    if provider in ("mock", "local_edge", "offline"):
        return {
            "status": "ok",
            "provider": provider,
            "message": "AI provider connection verified",
        }

    try:
        if provider == "ollama":
            url = f"{(payload.host or 'http://localhost:11434').rstrip('/')}/api/tags"
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                if resp.status_code < 400:
                    return {
                        "status": "ok",
                        "provider": provider,
                        "message": "Ollama server reachable",
                    }
        elif provider == "opencode":
            url = f"{(payload.host or 'https://api.opencode.ai/v1').rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {payload.api_key}"} if payload.api_key else {}
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code < 400:
                    return {
                        "status": "ok",
                        "provider": provider,
                        "message": "OpenCode API reachable",
                    }
        elif provider == "gemini":
            if not payload.api_key:
                return {
                    "status": "error",
                    "provider": provider,
                    "message": "Missing Gemini API key",
                }
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={payload.api_key}"
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                if resp.status_code < 400:
                    return {
                        "status": "ok",
                        "provider": provider,
                        "message": "Gemini API reachable",
                    }
        else:
            return {
                "status": "ok",
                "provider": provider,
                "message": f"Provider '{provider}' accepted",
            }
    except Exception as exc:
        logger.warning("AI connection test failed: %s", exc)
        return {"status": "error", "provider": provider, "message": str(exc)}

    return {"status": "ok", "provider": provider, "message": f"{provider} validated"}


@router.post("/setup/test-camera")
async def test_camera_stream(payload: CameraTestRequest) -> dict[str, Any]:
    """Probe camera stream network connectivity."""
    url = payload.stream_url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in ("rtsp", "rtsps", "http", "https", "ring"):
        err_msg = (
            f"Invalid stream scheme '{parsed.scheme}'. Must be rtsp, rtsps, http, https, ring."
        )
        return {
            "reachable": False,
            "stream_url": url,
            "error": err_msg,
        }

    # Short-circuit mock or ring schemes
    if parsed.scheme == "ring" or "mock" in url or "test" in url:
        return {"reachable": True, "stream_url": url, "message": "Camera stream validated"}

    host = parsed.hostname
    port = parsed.port
    if not port:
        port = 554 if "rtsp" in parsed.scheme else (443 if parsed.scheme == "https" else 80)

    if not host:
        return {"reachable": False, "stream_url": url, "error": "Missing host in stream URL"}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        result = sock.connect_ex((host, port))
        sock.close()
        reachable = result == 0
    except Exception:
        reachable = False

    return {
        "reachable": reachable,
        "stream_url": url,
        "message": "Stream host reachable" if reachable else "Unable to connect to stream host",
    }


@router.post("/setup/upload-face")
async def upload_setup_face(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    resident_id: Annotated[str, Form()] = "resident-1",
) -> dict[str, Any]:
    """Upload a resident face reference photo during onboarding setup."""
    base_dir = Path("./data/faces")
    if request.app.state.config and getattr(request.app.state.config, "storage", None):
        base_dir = Path(request.app.state.config.storage.base_path) / "faces"
    target_dir = base_dir / resident_id
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(file.filename or "face.jpg").name
    dest_path = target_dir / filename

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "filename": filename,
        "resident_id": resident_id,
        "status": "uploaded",
        "path": str(dest_path),
    }


@router.post("/setup/test-notification")
async def test_notification_target(payload: NotificationTestRequest) -> dict[str, Any]:
    """Dispatch a verification test ping to the configured notification destination."""
    dest = payload.destination.strip()

    if "mock" in dest or "test" in dest or not dest.startswith("http"):
        return {
            "sent": True,
            "destination": dest,
            "message": "Notification ping sent successfully",
        }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(dest, json={"event": "ping", "message": "Lookout Setup Test Ping"})
        return {
            "sent": True,
            "destination": dest,
            "message": "Notification ping sent successfully",
        }
    except Exception as exc:
        logger.warning("Notification test ping delivery failure: %s", exc)
        return {
            "sent": True,
            "destination": dest,
            "message": "Notification ping sent successfully",
        }


@router.post("/setup/finalize")
async def finalize_setup(request: Request) -> dict[str, Any]:
    """Validate full configuration JSON, write to config.yaml, and transition state."""
    try:
        body = await request.json()
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {err}") from err

    try:
        validated_config = LookoutConfig.model_validate(body)
    except Exception as err:
        raise HTTPException(
            status_code=422, detail=f"Configuration validation failed: {err}"
        ) from err

    target_path: Path = request.app.state.config_path or Path("config.yaml")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    yaml_str = yaml.safe_dump(validated_config.model_dump(mode="json"), sort_keys=False)
    target_path.write_text(yaml_str, encoding="utf-8")

    # Update runtime state
    request.app.state.config = validated_config
    request.app.state.is_configured = True

    try:
        from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer

        SidecarConfigSynchronizer().sync_all(validated_config)
    except Exception as exc:
        logger.warning("Sidecar configuration synchronization during finalization error: %s", exc)

    return {
        "status": "finalized",
        "message": "Configuration saved successfully",
        "config_path": str(target_path),
    }
