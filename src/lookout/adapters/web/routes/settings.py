"""REST API endpoints for runtime settings, AI cascade switching, and face gallery."""

import logging
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


class AiModeRequest(BaseModel):
    """Payload to dynamically mutate the runtime AI cascade mode."""

    mode: str = Field(..., description="Target AI mode: auto, local_only, gemini_only, off")


def _get_gallery_dir(request: Request) -> Path:
    """Resolve face gallery directory from configuration or default fallback."""
    config = request.app.state.config
    if config is not None:
        if getattr(config, "face_recognition", None) and config.face_recognition.gallery_dir:
            path = Path(config.face_recognition.gallery_dir)
        elif getattr(config, "storage", None) and config.storage.base_path:
            path = Path(config.storage.base_path) / "faces"
        else:
            path = Path("./data/faces")
    else:
        path = Path("./data/faces")
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/settings/ai-mode")
async def get_ai_mode(request: Request) -> dict[str, Any]:
    """Retrieve active runtime AI cascade inference mode."""
    vision_adapter = getattr(request.app.state, "vision_adapter", None)
    if vision_adapter is not None and hasattr(vision_adapter, "get_runtime_mode"):
        active_mode = vision_adapter.get_runtime_mode()
    else:
        active_mode = "auto"
    return {"active_mode": active_mode}


@router.post("/settings/ai-mode")
async def set_ai_mode(payload: AiModeRequest, request: Request) -> dict[str, Any]:
    """Dynamically switch runtime AI cascade inference mode without server restart."""
    vision_adapter = getattr(request.app.state, "vision_adapter", None)
    if vision_adapter is not None and hasattr(vision_adapter, "set_runtime_mode"):
        try:
            vision_adapter.set_runtime_mode(payload.mode)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
    return {"active_mode": payload.mode.lower(), "status": "updated"}


@router.get("/settings/faces")
async def list_faces(request: Request) -> dict[str, Any]:
    """List enrolled resident face images and household member profiles."""
    gallery_dir = _get_gallery_dir(request)
    faces: list[dict[str, str]] = []

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    for p in gallery_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in valid_exts:
            resident_id = p.parent.name if p.parent != gallery_dir else "unassigned"
            faces.append(
                {
                    "resident_id": resident_id,
                    "filename": p.name,
                    "path": str(p),
                }
            )

    config = request.app.state.config
    residents = (
        [r.model_dump() for r in config.residents]
        if config and hasattr(config, "residents")
        else []
    )
    pets = [p.model_dump() for p in config.pets] if config and hasattr(config, "pets") else []

    return {
        "faces": faces,
        "residents": residents,
        "pets": pets,
    }


@router.post("/settings/faces/upload")
async def upload_face(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    resident_id: Annotated[str, Form()] = "unassigned",
) -> dict[str, Any]:
    """Upload a resident face reference photo to the face gallery."""
    gallery_dir = _get_gallery_dir(request)
    target_dir = gallery_dir / resident_id
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


@router.delete("/settings/faces/{resident_id}/{filename}")
async def delete_face(
    resident_id: str,
    filename: str,
    request: Request,
) -> dict[str, Any]:
    """Delete an enrolled resident face reference photo."""
    gallery_dir = _get_gallery_dir(request)
    safe_filename = Path(filename).name

    candidates = [
        gallery_dir / resident_id / safe_filename,
        gallery_dir / safe_filename,
    ]

    deleted = False
    for path in candidates:
        if path.exists() and path.is_file():
            path.unlink()
            deleted = True
            break

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Face file '{filename}' not found")

    return {"deleted": True, "filename": safe_filename, "resident_id": resident_id}
