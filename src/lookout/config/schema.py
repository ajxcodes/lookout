"""Pydantic configuration schemas with strict cross-field validation."""

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lookout.domain.models import EventCategory, ThreatLevel


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
    camera_type: str = Field(
        default="generic", description="Camera adapter type (e.g., generic, ring, go2rtc)"
    )
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
        if parsed.scheme not in ("rtsp", "rtsps", "http", "https", "ring"):
            raise ValueError(
                f"Invalid stream URL scheme '{parsed.scheme}'. "
                "Must be rtsp, rtsps, http, https, or ring."
            )
        if not parsed.netloc and not parsed.path:
            raise ValueError("Invalid stream URL: missing host or network location.")
        return v


class Go2RtcConfig(BaseModel):
    """Configuration for go2rtc streaming server."""

    model_config = ConfigDict(extra="forbid")

    api_url: str = Field(
        default="http://localhost:1984", description="Base URL for go2rtc REST API"
    )
    timeout_seconds: float = Field(
        default=10.0, gt=0.0, description="HTTP request and stream timeout in seconds"
    )


class RingConfig(BaseModel):
    """Configuration for Ring cloud adapter."""

    model_config = ConfigDict(extra="forbid")

    auth_token: str | None = Field(default=None, description="Ring authentication / refresh token")
    api_url: str = Field(default="https://api.ring.com", description="Ring REST API base endpoint")
    timeout_seconds: float = Field(
        default=15.0, gt=0.0, description="Timeout for Ring cloud API requests"
    )


class TranscodeConfig(BaseModel):
    """Configuration for FFmpeg media normalization and preview generation."""

    model_config = ConfigDict(extra="forbid")

    ffmpeg_path: str = Field(default="ffmpeg", description="Path to ffmpeg executable")
    ffprobe_path: str = Field(default="ffprobe", description="Path to ffprobe executable")
    use_stream_copy: bool = Field(
        default=True, description="Attempt direct stream-copy remux before re-encoding"
    )
    max_threads: int = Field(
        default=2, ge=1, description="Maximum CPU threads allowed for transcode subprocesses"
    )
    nice_level: int = Field(
        default=10, ge=-20, le=19, description="Process priority (niceness) for transcode workers"
    )
    temp_dir: str = Field(
        default="/dev/shm", description="Temporary scratch directory for transcoding"
    )
    gif_fps: int = Field(default=6, ge=1, description="Frame rate for preview GIF generation")
    gif_width: int = Field(
        default=320, ge=16, description="Pixel width for preview GIF downscaling"
    )
    gif_duration: float = Field(
        default=2.5, gt=0.0, description="Maximum duration in seconds for preview GIF"
    )
    thumbnail_count: int = Field(
        default=5, ge=1, description="Number of scrubbed thumbnail images to extract"
    )


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


class OllamaConfig(BaseModel):
    """Configuration for local Ollama LAN vision inference."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="http://localhost:11434", description="Ollama server endpoint")
    model: str = Field(default="moondream", description="Ollama vision model tag")
    timeout_seconds: float = Field(default=5.0, gt=0.0, description="Request timeout in seconds")
    enabled: bool = Field(default=True, description="Whether Ollama inference is active")


class OpenCodeConfig(BaseModel):
    """Configuration for OpenCode cloud vision inference (Go / Free)."""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, description="OpenCode API key")
    api_url: str = Field(
        default="https://api.opencode.ai/v1", description="OpenCode API base endpoint"
    )
    model_go: str = Field(default="deepseek-v4.1-flash", description="Model for OpenCode Go tier")
    model_free: str = Field(
        default="deepseek-v4.1-flash-free", description="Model for OpenCode Free tier"
    )
    timeout_seconds: float = Field(default=8.0, gt=0.0, description="Request timeout in seconds")
    enabled: bool = Field(default=True, description="Whether OpenCode inference is active")


class GeminiConfig(BaseModel):
    """Configuration for Google Gemini cloud multimodal vision inference."""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, description="Google Gemini API key")
    api_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Gemini API base endpoint",
    )
    model: str = Field(default="gemini-3.5-flash-lite", description="Gemini model name")
    timeout_seconds: float = Field(default=8.0, gt=0.0, description="Request timeout in seconds")
    enabled: bool = Field(default=True, description="Whether Gemini inference is active")


class CascadeConfig(BaseModel):
    """Configuration for multi-tier vision inference cascade."""

    model_config = ConfigDict(extra="forbid")

    tiers: list[str] = Field(
        default_factory=lambda: [
            "ollama",
            "opencode_go",
            "gemini",
            "opencode_free",
            "local_edge",
            "offline",
        ],
        description="Ordered sequence of inference tier identifiers",
    )
    total_timeout_seconds: float = Field(
        default=25.0, gt=0.0, description="Cumulative timeout across all tiers"
    )
    fallback_category: EventCategory = Field(
        default=EventCategory.UNKNOWN_VISITOR,
        description="Default fallback category when all tiers fail",
    )


class FaceRecognitionConfig(BaseModel):
    """Configuration for local biometric face recognition."""

    model_config = ConfigDict(extra="forbid")

    similarity_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, description="Cosine similarity threshold for resident match"
    )
    model_name: str = Field(default="facenet-light", description="Face embedding model identifier")
    gallery_dir: str | None = Field(default=None, description="Optional local directory for faces")
    enabled: bool = Field(default=True, description="Whether face recognition is active")


class ImmichConfig(BaseModel):
    """Configuration for self-hosted Immich gallery synchronization."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(default="http://localhost:2283", description="Immich instance base URL")
    api_key: str | None = Field(default=None, description="Immich API key")
    sync_interval_hours: int = Field(default=24, ge=1, description="Hours between sync cycles")
    include_persons: list[str] = Field(
        default_factory=list, description="Person names to include in sync (empty syncs all)"
    )
    enabled: bool = Field(default=True, description="Whether Immich sync is active")


class AudioClassifierConfig(BaseModel):
    """Configuration for acoustic event classification."""

    model_config = ConfigDict(extra="forbid")

    model_path: str = Field(default="models/yamnet.tflite", description="YAMNet model file path")
    confidence_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Minimum confidence for acoustic events"
    )
    sample_rate: int = Field(default=16000, gt=0, description="Expected audio sample rate in Hz")
    emergency_labels: list[str] = Field(
        default_factory=lambda: ["glass_break", "alarm", "smoke_alarm", "gunshot", "scream"],
        description="Sound labels categorized as emergencies",
    )
    pet_labels: list[str] = Field(
        default_factory=lambda: ["dog_bark", "dog_whimper", "cat_meow"],
        description="Sound labels categorized as pet activity",
    )
    enabled: bool = Field(default=True, description="Whether audio classification is active")


class LookoutConfig(BaseModel):
    """Top-level configuration schema for Lookout."""

    model_config = ConfigDict(extra="forbid")

    system_name: str = Field(default="Lookout", description="System installation name")
    residents: list[ResidentConfig] = Field(default_factory=list)
    pets: list[PetConfig] = Field(default_factory=list)
    cameras: list[CameraConfig] = Field(default_factory=list)
    notification_targets: list[NotificationTargetConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    go2rtc: Go2RtcConfig = Field(default_factory=Go2RtcConfig)
    ring: RingConfig = Field(default_factory=RingConfig)
    transcode: TranscodeConfig = Field(default_factory=TranscodeConfig)
    cascade: CascadeConfig = Field(default_factory=CascadeConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    face_recognition: FaceRecognitionConfig = Field(default_factory=FaceRecognitionConfig)
    immich: ImmichConfig = Field(default_factory=ImmichConfig)
    audio_classifier: AudioClassifierConfig = Field(default_factory=AudioClassifierConfig)

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
