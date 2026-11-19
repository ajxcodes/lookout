"""REST API endpoints for continuous recordings browsing and attachment."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["recordings"])


def _base_path(request: Request) -> Path:
    cfg = getattr(request.app.state, "config", None)
    if cfg is not None and getattr(cfg, "storage", None) is not None:
        return Path(cfg.storage.base_path)
    return Path("./data")


def _recorder(request: Request) -> Any:
    return getattr(request.app.state, "continuous_recorder", None)


def _collect_segments(
    base: Path, camera_id: str | None = None, date: str | None = None
) -> list[dict[str, Any]]:
    cont = base / "continuous"
    if not cont.exists():
        return []
    result: list[dict[str, Any]] = []
    roots = [cont / camera_id] if camera_id else [p for p in cont.iterdir() if p.is_dir()]
    for root in roots:
        if not root.exists():
            continue
        for seg in root.rglob("*.mp4"):
            if not seg.is_file():
                continue
            rel = seg.relative_to(base).as_posix()
            if date:
                # date may be YYYY-MM-DD or YYYY/MM/DD
                norm = date.replace("-", "/")
                if norm not in rel and date not in rel:
                    continue
            result.append(
                {
                    "camera_id": seg.relative_to(cont).parts[0] if cont in seg.parents else None,
                    "path": rel,
                    "segment_path": str(seg),
                    "media_uri": f"/media/{rel}",
                    "size": seg.stat().st_size,
                    "mtime": seg.stat().st_mtime,
                }
            )
    result.sort(key=lambda x: x.get("mtime", 0))
    return result


class AttachRequest(BaseModel):
    segment_path: str | None = None
    segment: str | None = None
    file_path: str | None = None
    path: str | None = None
    event_id: str | None = None
    event: str | None = None
    camera_id: str | None = None


def _segment_value(body: AttachRequest) -> str | None:
    return body.segment_path or body.segment or body.file_path or body.path


def _event_value(body: AttachRequest) -> str | None:
    return body.event_id or body.event


async def _recorder_status(request: Request) -> Any:
    rec = _recorder(request)
    if rec is None:
        return {}
    status = rec.status()
    if inspect.isawaitable(status):
        status = await status
    return status


@router.get("/recordings/status")
@router.get("/recorder/status")
@router.get("/continuous/status")
@router.get("/continuous-recording/status")
@router.get("/recordings/recorder/status")
async def recorder_status(request: Request) -> Any:
    return await _recorder_status(request)


@router.get("/recordings/segments")
@router.get("/recordings")
@router.get("/continuous/segments")
@router.get("/continuous/recordings")
async def list_segments(
    request: Request,
    camera_id: str | None = None,
    camera: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    base = _base_path(request)
    cid = camera_id or camera
    segments = _collect_segments(base, cid, date)
    return {"segments": segments, "count": len(segments)}


@router.get("/recordings/segments/{camera_id}")
async def list_segments_by_camera(
    request: Request, camera_id: str, date: str | None = None
) -> dict[str, Any]:
    base = _base_path(request)
    segments = _collect_segments(base, camera_id, date)
    return {"segments": segments, "count": len(segments)}


async def _attach(request: Request, body: AttachRequest) -> Any:
    base = _base_path(request)
    seg_val = _segment_value(body)
    if not seg_val:
        raise HTTPException(status_code=400, detail="segment_path required")
    seg_path = Path(seg_val)
    if not seg_path.is_absolute():
        # Could be relative media path or relative to base.
        candidates = [base / seg_val, Path(seg_val), base / seg_val.lstrip("/")]
        for c in candidates:
            if c.is_file():
                seg_path = c
                break
    if not seg_path.is_file():
        # Try media resolver style: strip /media/
        cleaned = seg_val
        if cleaned.startswith("/media/"):
            cleaned = cleaned[len("/media/") :]
        cand = base / cleaned
        if cand.is_file():
            seg_path = cand
    if not seg_path.is_file():
        raise HTTPException(status_code=404, detail="segment not found")

    repo = getattr(request.app.state, "repository", None)
    event_id = _event_value(body)
    event = None
    if repo is not None and event_id is not None:
        getter = getattr(repo, "get_by_id", None) or getattr(repo, "get", None)
        if callable(getter):
            try:
                event = getter(event_id)
                if inspect.isawaitable(event):
                    event = await event
            except Exception:
                event = None

    # Determine destination event media path.
    events_dir = base / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{seg_path.stem}_attached{seg_path.suffix}"
    # avoid collision
    i = 1
    dest = events_dir / dest_name
    while dest.exists():
        dest = events_dir / f"{seg_path.stem}_attached_{i}{seg_path.suffix}"
        i += 1
    shutil.copy2(seg_path, dest)

    media_uri = str(dest)
    # Also provide /media path? Keep filesystem path to satisfy Path.exists().

    if event is not None:
        event.media_uri = media_uri
        saver = getattr(repo, "save", None)
        if callable(saver):
            try:
                res = saver(event)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass
        # Return serialized event if possible.
        try:
            from lookout.adapters.web.serialization import serialize_event

            return {"status": "ok", "event": serialize_event(event), "media_uri": media_uri}
        except Exception:
            return {
                "status": "ok",
                "event": {"id": getattr(event, "id", None), "media_uri": media_uri},
                "media_uri": media_uri,
            }

    # If no event, create a lightweight response.
    return {"status": "ok", "media_uri": media_uri, "segment_path": str(seg_path)}


@router.post("/recordings/attach")
@router.post("/recordings/attach-segment")
@router.post("/recordings/segments/attach")
@router.post("/recordings/attach_segment")
async def attach_segment(request: Request) -> Any:
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}
    body = AttachRequest(**(body_json if isinstance(body_json, dict) else {}))
    return await _attach(request, body)


@router.post("/recordings/{camera_id}/attach")
async def attach_segment_camera(request: Request, camera_id: str) -> Any:
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}
    data = body_json if isinstance(body_json, dict) else {}
    data.setdefault("camera_id", camera_id)
    body = AttachRequest(**data)
    return await _attach(request, body)
