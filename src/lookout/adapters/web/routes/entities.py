"""REST API endpoints for entity reference catalog (people, pets, vehicles, things)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from lookout.adapters.sqlite.members import HouseholdMember, HouseholdMembersRepository

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
MAX_PHOTO_SIZE: int = 15 * 1024 * 1024  # 15MB
CHUNK_SIZE: int = 64 * 1024  # 64KB

router = APIRouter(tags=["entities"])


class EntityPayload(BaseModel):
    id: str
    name: str
    entity_type: str = "resident"  # resident, pet, vehicle, thing
    species: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    enrolled_photos: list[str] = Field(default_factory=list)
    enrolled_faces: list[str] = Field(default_factory=list)


def _get_entity_photos_dir(request: Request, entity_id: str) -> Path:
    config = getattr(request.app.state, "config", None)
    base_dir = Path("./data/entities")
    if config is not None and getattr(config, "storage", None) and config.storage.base_path:
        base_dir = Path(config.storage.base_path) / "entities"
    entity_dir = base_dir / entity_id
    entity_dir.mkdir(parents=True, exist_ok=True)
    return entity_dir


@router.get("/entities")
async def list_entities(request: Request) -> dict[str, Any]:
    """List all enrolled entities across People, Pets, Vehicles, and Things."""
    entities: list[dict[str, Any]] = []

    # 1. From members repository if present
    repo: HouseholdMembersRepository | None = getattr(request.app.state, "members_repo", None)
    if repo is not None:
        try:
            members = await repo.list_members()
            for m in members:
                entities.append(
                    {
                        "id": m.id,
                        "name": m.name,
                        "entity_type": m.member_type,
                        "species": m.species,
                        "attributes": m.attributes,
                        "enrolled_faces": m.enrolled_faces,
                        "enrolled_photos": m.enrolled_faces,
                    }
                )
        except Exception as exc:
            logger.warning("Could not list members from repo: %s", exc)

    # 2. From config if repo empty or not available
    config = getattr(request.app.state, "config", None)
    if config is not None:
        existing_ids = {e["id"] for e in entities}
        for r in getattr(config, "residents", []):
            if r.id not in existing_ids:
                entities.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "entity_type": "resident",
                        "attributes": {},
                        "enrolled_faces": r.enrolled_faces,
                        "enrolled_photos": r.enrolled_faces,
                    }
                )
                existing_ids.add(r.id)
        for p in getattr(config, "pets", []):
            if p.id not in existing_ids:
                entities.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "entity_type": "pet",
                        "species": p.species,
                        "attributes": {
                            "breed": getattr(p, "breed", None),
                            "color": getattr(p, "color", None),
                        },
                        "enrolled_photos": getattr(p, "enrolled_photos", []),
                        "enrolled_faces": [],
                    }
                )
                existing_ids.add(p.id)
        for v in getattr(config, "vehicles", []):
            if v.id not in existing_ids:
                entities.append(
                    {
                        "id": v.id,
                        "name": v.name,
                        "entity_type": "vehicle",
                        "attributes": {
                            "make": v.make,
                            "model": v.model,
                            "color": v.color,
                            "license_plate": v.license_plate,
                        },
                        "enrolled_photos": v.enrolled_photos,
                        "enrolled_faces": [],
                    }
                )
                existing_ids.add(v.id)

    # 3. Check runtime custom entities
    memory_entities: dict[str, dict[str, Any]] = getattr(request.app.state, "custom_entities", {})
    existing_ids = {e["id"] for e in entities}
    for eid, edata in memory_entities.items():
        if eid not in existing_ids:
            entities.append(edata)

    return {"entities": entities}


@router.post("/entities")
async def create_or_update_entity(payload: EntityPayload, request: Request) -> dict[str, Any]:
    """Create or update an entity reference profile."""
    if not hasattr(request.app.state, "custom_entities"):
        request.app.state.custom_entities = {}

    entity_dict = {
        "id": payload.id,
        "name": payload.name,
        "entity_type": payload.entity_type,
        "species": payload.species,
        "attributes": payload.attributes,
        "enrolled_photos": payload.enrolled_photos or payload.enrolled_faces,
        "enrolled_faces": payload.enrolled_faces,
    }
    request.app.state.custom_entities[payload.id] = entity_dict

    repo: HouseholdMembersRepository | None = getattr(request.app.state, "members_repo", None)
    if repo is not None:
        try:
            member = HouseholdMember(
                id=payload.id,
                name=payload.name,
                member_type=payload.entity_type,
                species=payload.species,
                enrolled_faces=payload.enrolled_photos or payload.enrolled_faces,
                attributes=payload.attributes,
            )
            await repo.save(member)
        except Exception as exc:
            logger.warning("Failed to save member to repo: %s", exc)

    # Sync into domain entity matcher if present
    svc = getattr(request.app.state, "event_service", None)
    if svc is not None and getattr(svc, "entity_matcher", None) is not None:
        matcher = svc.entity_matcher
        if payload.entity_type == "vehicle":
            from lookout.config.schema import VehicleConfig

            matcher.register_vehicle(
                VehicleConfig(
                    id=payload.id,
                    name=payload.name,
                    make=payload.attributes.get("make"),
                    model=payload.attributes.get("model"),
                    color=payload.attributes.get("color"),
                    license_plate=payload.attributes.get("license_plate"),
                    enrolled_photos=payload.enrolled_photos,
                )
            )
        elif payload.entity_type == "pet":
            from lookout.config.schema import PetConfig

            matcher.register_pet(
                PetConfig(
                    id=payload.id,
                    name=payload.name,
                    species=payload.species or "dog",
                    breed=payload.attributes.get("breed"),
                    color=payload.attributes.get("color"),
                    enrolled_photos=payload.enrolled_photos,
                )
            )
        elif payload.entity_type == "resident":
            from lookout.config.schema import ResidentConfig

            matcher.register_resident(
                ResidentConfig(
                    id=payload.id,
                    name=payload.name,
                    enrolled_faces=payload.enrolled_faces or payload.enrolled_photos,
                )
            )

    return {"status": "ok", "entity": entity_dict, "id": payload.id}


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str, request: Request) -> dict[str, Any]:
    """Retrieve details of a single entity."""
    res = await list_entities(request)
    for ent in res.get("entities", []):
        if ent["id"] == entity_id:
            return cast(dict[str, Any], ent)
    raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")


@router.delete("/entities/{entity_id}")
async def delete_entity(entity_id: str, request: Request) -> dict[str, Any]:
    """Delete an entity profile."""
    if (
        hasattr(request.app.state, "custom_entities")
        and entity_id in request.app.state.custom_entities
    ):
        del request.app.state.custom_entities[entity_id]

    repo: HouseholdMembersRepository | None = getattr(request.app.state, "members_repo", None)
    if repo is not None:
        await repo.delete(entity_id)

    config = getattr(request.app.state, "config", None)
    if config is not None:
        config.residents = [r for r in config.residents if r.id != entity_id]
        config.pets = [p for p in config.pets if p.id != entity_id]
        config.vehicles = [v for v in config.vehicles if v.id != entity_id]

    return {"status": "ok", "deleted": True, "id": entity_id}


@router.post("/entities/{entity_id}/photos")
async def upload_entity_photo(
    entity_id: str,
    request: Request,
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    """Upload a reference photo for an entity."""
    filename = Path(file.filename or "photo.jpg").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{suffix}'. Allowed extensions: {allowed}",
        )

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail="File too large. Max size is 15MB.",
        )
    if file.size is not None and file.size > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail="File too large. Max size is 15MB.",
        )

    target_dir = _get_entity_photos_dir(request, entity_id)
    dest_path = target_dir / filename

    total_size = 0
    try:
        with dest_path.open("wb") as buffer:
            while chunk := await file.read(CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > MAX_PHOTO_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="File too large. Max size is 15MB.",
                    )
                buffer.write(chunk)
    except HTTPException:
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        raise

    return {
        "status": "ok",
        "filename": filename,
        "path": f"/media/entities/{entity_id}/{filename}",
    }


@router.delete("/entities/{entity_id}/photos/{filename}")
async def delete_entity_photo(
    entity_id: str,
    filename: str,
    request: Request,
) -> dict[str, Any]:
    """Delete a reference photo for an entity."""
    safe_filename = Path(filename).name
    target_dir = _get_entity_photos_dir(request, entity_id)
    target_file = target_dir / safe_filename
    if target_file.exists():
        target_file.unlink()
        return {"status": "ok", "deleted": True, "filename": safe_filename}
    raise HTTPException(
        status_code=404, detail=f"Photo '{safe_filename}' not found for entity '{entity_id}'"
    )
