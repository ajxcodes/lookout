"""Pydantic configuration schemas with strict cross-field validation."""

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lookout.domain.models import ThreatLevel


class ResidentConfig(BaseModel):
    """Configuration for an enrolled household resident."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique resident identifier")
    name: str = Field(..., description="Full or display name")
    notification_targets: list[str] = Field(
        default_factory=list, description="Target names to alert for this resident"
    )
    enrolled_faces: list[str] = Field(
        default_factory=list, description="List of face embedding or reference image paths"
    )


class PetConfig(BaseModel):
    """Configuration for an enrolled household pet."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique pet identifier")
    name: str = Field(..., description="Pet name")
    species: str = Field(default="dog", description="Animal species (e.g., dog, cat)")


class CameraConfig(BaseModel):
    """Configuration for an ingested camera feed."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique camera identifier")
    name: str = Field(..., description="Human-readable camera name")
    stream_url: str = Field(..., description="RTSP, HTTP, or HTTPS stream endpoint")
    enabled: bool = Field(default=True, description="Whether camera feed ingestion is active")
    location: str | None = Field(default=None, description="Physical location (e.g., Front Porch)")
    sensitivity: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detection trigger sensitivity"
    )
    record_duration: float = Field(
        default=10.0, gt=0.0, description="Duration in seconds for captured video clips"
    )

    @field_validator("stream_url")
    @classmethod
    def validate_stream_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
            raise ValueError(
                f"Invalid stream URL scheme '{parsed.scheme}'. Must be rtsp, rtsps, http, or https."
            )
        if not parsed.netloc and not parsed.path:
            raise ValueError("Invalid stream URL: missing host or network location.")
        return v


class NotificationTargetConfig(BaseModel):
    """Configuration for external alert dispatch destinations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique target name identifier")
    target_type: str = Field(..., description="Target type (webhook, mobile, ntfy, etc.)")
    destination: str = Field(..., description="Destination endpoint, phone, or token")
    min_threat_level: ThreatLevel = Field(
        default=ThreatLevel.LOW, description="Minimum threat level to trigger dispatch"
    )
    enabled: bool = Field(default=True, description="Whether notification target is active")

    @field_validator("min_threat_level", mode="before")
    @classmethod
    def parse_threat_level(cls, v: Any) -> ThreatLevel:
        if isinstance(v, ThreatLevel):
            return v
        if isinstance(v, int):
            return ThreatLevel(v)
        if isinstance(v, str):
            try:
                return ThreatLevel[v.upper()]
            except KeyError as err:
                raise ValueError(f"Unknown threat level '{v}'") from err
        raise ValueError(f"Cannot parse threat level from {v}")


class StorageConfig(BaseModel):
    """Storage and retention limits configuration."""

    model_config = ConfigDict(extra="forbid")

    base_path: str = Field(default="./data", description="Root directory for local data")
    database_path: str = Field(
        default="./data/lookout.db", description="Path to SQLite database file"
    )
    wal_mode: bool = Field(default=True, description="Enable SQLite Write-Ahead Logging (WAL) mode")
    routine_retention_days: int = Field(
        default=7, ge=1, description="Days to retain routine non-threat events and media"
    )
    critical_retention_days: int = Field(
        default=30, ge=1, description="Days to retain critical threat events and media"
    )
    busy_timeout_ms: int = Field(
        default=5000, ge=0, description="SQLite busy handler timeout in milliseconds"
    )
    retention_days: int = Field(default=30, ge=1, description="Days to retain events and clips")
    max_storage_gb: float = Field(
        default=100.0, gt=0.0, description="Max disk allocation in gigabytes"
    )


class LookoutConfig(BaseModel):
    """Top-level configuration schema for Lookout."""

    model_config = ConfigDict(extra="forbid")

    system_name: str = Field(default="Lookout", description="System installation name")
    residents: list[ResidentConfig] = Field(default_factory=list)
    pets: list[PetConfig] = Field(default_factory=list)
    cameras: list[CameraConfig] = Field(default_factory=list)
    notification_targets: list[NotificationTargetConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @model_validator(mode="after")
    def validate_cross_field_invariants(self) -> "LookoutConfig":
        # 1. Unique camera IDs
        camera_ids = [c.id for c in self.cameras]
        if len(camera_ids) != len(set(camera_ids)):
            duplicates = [cid for cid in camera_ids if camera_ids.count(cid) > 1]
            raise ValueError(f"Duplicate camera ID(s) found: {set(duplicates)}")

        # 2. Unique resident IDs
        resident_ids = [r.id for r in self.residents]
        if len(resident_ids) != len(set(resident_ids)):
            duplicates = [rid for rid in resident_ids if resident_ids.count(rid) > 1]
            raise ValueError(f"Duplicate resident ID(s) found: {set(duplicates)}")

        # 3. Unique pet IDs
        pet_ids = [p.id for p in self.pets]
        if len(pet_ids) != len(set(pet_ids)):
            duplicates = [pid for pid in pet_ids if pet_ids.count(pid) > 1]
            raise ValueError(f"Duplicate pet ID(s) found: {set(duplicates)}")

        # 4. Unique notification target names
        target_names = [t.name for t in self.notification_targets]
        if len(target_names) != len(set(target_names)):
            duplicates = [name for name in target_names if target_names.count(name) > 1]
            raise ValueError(f"Duplicate notification target name(s) found: {set(duplicates)}")

        # 5. Resident notification targets exist in notification_targets
        valid_targets = set(target_names)
        for resident in self.residents:
            for target in resident.notification_targets:
                if target not in valid_targets:
                    raise ValueError(
                        f"Resident '{resident.id}' references undefined "
                        f"notification target '{target}'"
                    )

        return self
