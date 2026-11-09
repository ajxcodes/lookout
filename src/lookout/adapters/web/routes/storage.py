"""REST API endpoints for storage status telemetry, configuration, and manual eviction."""

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lookout.adapters.storage_service import StorageService
from lookout.config.schema import StorageConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["storage"])


class StorageConfigUpdate(BaseModel):
    """Schema for partial or full updates to storage configuration."""

    max_storage_gb: float | None = Field(default=None, gt=0.0)
    max_storage_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    retention_mode: str | None = Field(
        default=None, pattern="^(fifo_oldest_first|events_priority)$"
    )
    preserve_bookmarked: bool | None = None
    cleanup_interval_seconds: float | None = Field(default=None, gt=0.0)
    low_watermark_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    warning_threshold_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    archive_enabled: bool | None = None
    archive_path: str | None = None
    archive_max_storage_gb: float | None = Field(default=None, gt=0.0)
    archive_max_storage_percent: float | None = Field(default=None, gt=0.0, le=100.0)


class StorageCleanRequest(BaseModel):
    """Optional payload for manual storage cleanup triggering."""

    target_bytes: int | None = None


@router.get("/storage/status")
async def get_storage_status(request: Request) -> dict[str, Any]:
    """Retrieve full dual-tier storage status, quota limits, and runway telemetry."""
    service: StorageService | None = getattr(request.app.state, "storage_service", None)
    if not service:
        # Fallback if storage service not yet initialized
        config = getattr(request.app.state, "config", None)
        storage_config = (
            config.storage if config and hasattr(config, "storage") else StorageConfig()
        )
        service = StorageService(config=storage_config, repository=request.app.state.repository)
        request.app.state.storage_service = service

    status = service.get_storage_status()
    return dataclasses.asdict(status)


@router.get("/storage/config")
async def get_storage_config(request: Request) -> dict[str, Any]:
    """Retrieve active storage configuration parameters."""
    config = getattr(request.app.state, "config", None)
    if config and hasattr(config, "storage"):
        storage_val: StorageConfig = config.storage
        return storage_val.model_dump()
    service: StorageService | None = getattr(request.app.state, "storage_service", None)
    if service:
        return service.config.model_dump()
    return StorageConfig().model_dump()


@router.post("/storage/config")
async def update_storage_config(payload: StorageConfigUpdate, request: Request) -> dict[str, Any]:
    """Dynamically update storage quota limits and retention settings."""
    app_config = getattr(request.app.state, "config", None)
    service: StorageService | None = getattr(request.app.state, "storage_service", None)

    current_data = (
        app_config.storage.model_dump()
        if (app_config and hasattr(app_config, "storage"))
        else (service.config.model_dump() if service else StorageConfig().model_dump())
    )

    update_dict = payload.model_dump(exclude_unset=True)
    current_data.update(update_dict)

    try:
        new_storage_config = StorageConfig(**current_data)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid storage configuration: {exc}"
        ) from exc

    if app_config and hasattr(app_config, "storage"):
        app_config.storage = new_storage_config
    if service:
        service.config = new_storage_config
    elif hasattr(request.app.state, "repository"):
        service = StorageService(config=new_storage_config, repository=request.app.state.repository)
        request.app.state.storage_service = service

    return new_storage_config.model_dump()


@router.post("/storage/clean")
async def trigger_storage_clean(
    request: Request, payload: StorageCleanRequest | None = None
) -> dict[str, Any]:
    """Manually invoke FIFO eviction down to target safe buffer across tiers."""
    service: StorageService | None = getattr(request.app.state, "storage_service", None)
    if not service:
        config = getattr(request.app.state, "config", None)
        storage_config = (
            config.storage if config and hasattr(config, "storage") else StorageConfig()
        )
        service = StorageService(config=storage_config, repository=request.app.state.repository)
        request.app.state.storage_service = service

    target_bytes = payload.target_bytes if payload else None
    result = await service.execute_fifo_eviction(target_bytes=target_bytes)
    return dataclasses.asdict(result)
