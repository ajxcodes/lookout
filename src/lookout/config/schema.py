"""Pydantic configuration schemas with strict cross-field validation."""

from typing import Any, Literal
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
    breed: str | None = Field(default=None, description="Animal breed")
    color: str | None = Field(default=None, description="Coat color")
    enrolled_photos: list[str] = Field(default_factory=list, description="List of reference photos")


class VehicleConfig(BaseModel):
    """Configuration for an enrolled household vehicle."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique vehicle identifier")
    name: str = Field(..., description="Vehicle name or label (e.g. Silver Honda Civic)")
    make: str | None = Field(default=None, description="Vehicle make (e.g. Honda)")
    model: str | None = Field(default=None, description="Vehicle model (e.g. Civic)")
    color: str | None = Field(default=None, description="Vehicle color (e.g. silver)")
    license_plate: str | None = Field(default=None, description="License plate number")
    enrolled_photos: list[str] = Field(
        default_factory=list, description="List of reference photo paths or filenames"
    )


class PolygonZoneConfig(BaseModel):
    """Configuration for an interactive spatial camera polygon zone."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique zone identifier")
    name: str = Field(..., description="Human-readable zone name (e.g. Backyard (Lawn))")
    camera_id: str | None = Field(
        default=None, description="Associated camera identifier if camera-specific"
    )
    polygon: list[tuple[float, float]] = Field(
        ..., description="Normalized (x, y) coordinates defining polygon perimeter"
    )

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.name == other or self.id == other
        return super().__eq__(other)


class LightMLConfig(BaseModel):
    """Configuration for micro-footprint Light ML object and posture detector."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Whether Light ML detection is active")
    model_path: str = Field(
        default="models/yolov8n.onnx", description="Micro-footprint ONNX model path"
    )
    confidence_threshold: float = Field(
        default=0.45, ge=0.0, le=1.0, description="Detection confidence threshold"
    )
    glare_rejection: bool = Field(
        default=True, description="Reject non-physical glare and headlight sweeps"
    )
    posture_detection: bool = Field(
        default=True, description="Enable pet elimination posture detection"
    )
    max_entities: int = Field(
        default=20, ge=1, description="Maximum concurrent bounding box detections"
    )


class PtzPresetConfig(BaseModel):
    """Seed preset configuration for a PTZ camera."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Preset name")
    pan: float = Field(default=0.0, ge=0.0, le=1.0, description="Normalized pan position")
    tilt: float = Field(default=0.0, ge=0.0, le=1.0, description="Normalized tilt position")
    zoom: float = Field(default=0.0, ge=0.0, le=1.0, description="Normalized zoom position")


class CameraConfig(BaseModel):
    """Configuration for an ingested camera feed."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique camera identifier")
    name: str = Field(..., description="Human-readable camera name")
    stream_url: str = Field(..., description="RTSP, HTTP, or HTTPS stream endpoint")
    camera_type: str = Field(
        default="generic", description="Camera adapter type (e.g., generic, ring, go2rtc)"
    )
    provider: str | None = Field(
        default=None, description="Camera provider brand (e.g. ring, wyze, generic)"
    )
    stream_mode: str | None = Field(
        default=None, description="Camera stream delivery mode (e.g. on_demand, live_rtc)"
    )
    enabled: bool = Field(default=True, description="Whether camera feed ingestion is active")
    notifications_muted: bool = Field(
        default=False, description="Whether camera notifications are muted"
    )
    mqtt_id: str | None = Field(
        default=None,
        description="Device id used on MQTT bridge topics (e.g. ring-mqtt device id)",
    )
    location: str | None = Field(default=None, description="Physical location (e.g., Front Porch)")
    sensitivity: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detection trigger sensitivity"
    )
    record_duration: float = Field(
        default=10.0, gt=0.0, description="Duration in seconds for captured video clips"
    )
    record_audio: bool | None = Field(
        default=None,
        description=(
            "Whether to include and transcode audio in event recordings "
            "(None inherits transcode.record_audio)"
        ),
    )
    timestamp_overlay: bool | None = Field(
        default=None,
        description="Whether to render session timestamp overlay (auto-enabled for Ring)",
    )
    ptz_enabled: bool = Field(default=False, description="Whether PTZ control is enabled")
    ptz_protocol: Literal["go2rtc-wyze", "go2rtc-onvif", "mqtt-wyze", "none"] = Field(
        default="none", description="PTZ control protocol"
    )
    ptz_cam_name: str | None = Field(
        default=None,
        description="Bridge camera name for PTZ topic (defaults to camera id)",
    )
    ptz_max_speed: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Maximum normalized PTZ speed"
    )
    ptz_presets: list[PtzPresetConfig] = Field(default_factory=list, description="Seed PTZ presets")
    native_provider: str | None = Field(
        default=None, description="Native ingestion provider (wyze/ring) for this camera"
    )
    native_device_id: str | None = Field(
        default=None, description="Native provider device id for this camera"
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


class WyzeConfig(BaseModel):
    """Configuration for Wyze cloud and bridge adapter."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, description="Wyze account email")
    password: str | None = Field(default=None, description="Wyze account password")
    key_id: str | None = Field(default=None, description="Wyze API Key ID")
    api_key: str | None = Field(default=None, description="Wyze API Key")
    totp_key: str | None = Field(default=None, description="Wyze TOTP secret key for automatic 2FA")
    auth_token: str | None = Field(default=None, description="Wyze API access token")
    refresh_token: str | None = Field(default=None, description="Wyze API refresh token")
    external_bridge_url: str | None = Field(
        default=None, description="External docker-wyze-bridge Web API endpoint"
    )
    bridge_rtsp_url: str | None = Field(
        default=None, description="External docker-wyze-bridge RTSP endpoint"
    )
    enabled: bool = Field(default=True, description="Whether Wyze bridge integration is active")
    native_ingestion: bool = Field(
        default=False, description="Whether native Wyze ingestion is enabled"
    )
    native_device_ids: list[str] = Field(
        default_factory=list, description="Discovered native Wyze device ids"
    )
    discovery_enabled: bool = Field(
        default=True, description="Whether native Wyze device discovery is enabled"
    )


class RingConfig(BaseModel):
    """Configuration for Ring cloud adapter."""

    model_config = ConfigDict(extra="forbid")

    auth_token: str | None = Field(default=None, description="Ring authentication / refresh token")
    api_url: str = Field(default="https://api.ring.com", description="Ring REST API base endpoint")
    timeout_seconds: float = Field(
        default=15.0, gt=0.0, description="Timeout for Ring cloud API requests"
    )
    refresh_token: str | None = Field(
        default=None, description="Ring OAuth refresh token (RINGTOKEN)"
    )
    email: str | None = Field(default=None, description="Ring account email")
    session_id: str | None = Field(default=None, description="Active 2FA challenge session ID")
    external_stream_url: str | None = Field(
        default=None, description="External ring-mqtt stream URL"
    )
    oauth_url: str = Field(
        default="https://oauth.ring.com", description="Ring OAuth authentication endpoint"
    )
    two_factor_auth_required: bool = Field(
        default=False, description="Whether 2FA challenge is currently pending"
    )
    enabled: bool = Field(default=True, description="Whether Ring bridge integration is active")
    native_ingestion: bool = Field(
        default=False, description="Whether native Ring ingestion is enabled"
    )
    native_device_ids: list[str] = Field(
        default_factory=list, description="Discovered native Ring device ids"
    )
    discovery_enabled: bool = Field(
        default=True, description="Whether native Ring device discovery is enabled"
    )
    timestamp_overlay: bool = Field(
        default=True,
        description="Whether to render an elapsed session timestamp overlay on Ring live streams",
    )


class TranscodeConfig(BaseModel):
    """Configuration for FFmpeg media normalization and preview generation."""

    model_config = ConfigDict(extra="forbid")

    ffmpeg_path: str = Field(default="ffmpeg", description="Path to ffmpeg executable")
    ffprobe_path: str = Field(default="ffprobe", description="Path to ffprobe executable")
    use_stream_copy: bool = Field(
        default=True, description="Attempt direct stream-copy remux before re-encoding"
    )
    hwaccel: str = Field(
        default="none",
        pattern="^(auto|vaapi|qsv|videotoolbox|nvenc|v4l2m2m|none)$",
        description=(
            "Hardware acceleration offload method "
            "(auto, vaapi, qsv, videotoolbox, nvenc, v4l2m2m, none)"
        ),
    )
    hwaccel_device: str | None = Field(
        default=None,
        description="Path to hardware device for acceleration (e.g. /dev/dri/renderD128)",
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
    record_audio: bool = Field(
        default=True,
        description="Whether to include and transcode audio (AAC) in event MP4 recordings",
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
        default=50.0, gt=0.0, description="Max disk allocation in gigabytes"
    )
    max_storage_percent: float = Field(
        default=80.0, gt=0.0, le=100.0, description="Max percentage of local volume"
    )
    retention_mode: str = Field(
        default="fifo_oldest_first",
        pattern="^(fifo_oldest_first|events_priority)$",
        description="Eviction strategy",
    )
    preserve_bookmarked: bool = Field(
        default=True, description="Protect bookmarked media from eviction on both tiers"
    )
    cleanup_interval_seconds: float = Field(
        default=300.0, gt=0.0, description="Daemon audit interval"
    )
    low_watermark_percent: float = Field(
        default=90.0, gt=0.0, le=100.0, description="Safe buffer target percentage of quota"
    )
    warning_threshold_percent: float = Field(
        default=90.0, gt=0.0, le=100.0, description="Warning alert trigger"
    )
    archive_enabled: bool = Field(
        default=False, description="Enable secondary tier archival to network share"
    )
    archive_path: str | None = Field(
        default=None, description="Path to mounted network share (e.g. NFS/SMB on ms)"
    )
    archive_max_storage_gb: float | None = Field(
        default=None, gt=0.0, description="Max disk allowance on archive server"
    )
    archive_max_storage_percent: float | None = Field(
        default=90.0, gt=0.0, le=100.0, description="Max percentage of archive volume"
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
    timeout_seconds: float = Field(default=15.0, gt=0.0, description="Request timeout in seconds")
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
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence required to accept tier result before cascading",
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


class MqttConfig(BaseModel):
    """Configuration for native MQTT camera-bridge event ingestion."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Whether MQTT event ingestion is active")
    host: str = Field(default="127.0.0.1", description="MQTT broker host")
    port: int = Field(default=1883, ge=1, le=65535, description="MQTT broker port")
    username: str | None = Field(default=None, description="MQTT username")
    password: str | None = Field(default=None, description="MQTT password")
    ring_topic: str = Field(default="ring", description="ring-mqtt base topic")
    wyze_topic: str = Field(default="wyzebridge", description="wyze-bridge base topic")
    cooldown_seconds: float = Field(
        default=30.0, ge=0.0, description="Minimum seconds between events per camera"
    )


class LookoutConfig(BaseModel):
    """Top-level configuration schema for Lookout."""

    model_config = ConfigDict(extra="forbid")

    system_name: str = Field(default="Lookout", description="System installation name")
    hardware_profile: str | None = Field(
        default=None,
        description="Declarative edge hardware sizing profile name (e.g. intel-n100-mini-pc)",
    )
    residents: list[ResidentConfig] = Field(default_factory=list)
    pets: list[PetConfig] = Field(default_factory=list)
    cameras: list[CameraConfig] = Field(default_factory=list)
    notification_targets: list[NotificationTargetConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    go2rtc: Go2RtcConfig = Field(default_factory=Go2RtcConfig)
    wyze: WyzeConfig = Field(default_factory=WyzeConfig)
    ring: RingConfig = Field(default_factory=RingConfig)
    transcode: TranscodeConfig = Field(default_factory=TranscodeConfig)
    cascade: CascadeConfig = Field(default_factory=CascadeConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    face_recognition: FaceRecognitionConfig = Field(default_factory=FaceRecognitionConfig)
    immich: ImmichConfig = Field(default_factory=ImmichConfig)
    audio_classifier: AudioClassifierConfig = Field(default_factory=AudioClassifierConfig)
    vehicles: list[VehicleConfig] = Field(default_factory=list)
    zones: list[PolygonZoneConfig] = Field(default_factory=list)
    light_ml: LightMLConfig = Field(default_factory=LightMLConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    public_url: str | None = Field(
        default=None, description="Public base URL of Lookout (used for notification links/media)"
    )
    home_assistant_url: str | None = Field(
        default=None,
        description="Home Assistant base URL; when set, notification links open its Lookout panel",
    )

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

        # 3b. Unique vehicle IDs
        vehicle_ids = [v.id for v in self.vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            duplicates = [vid for vid in vehicle_ids if vehicle_ids.count(vid) > 1]
            raise ValueError(f"Duplicate vehicle ID(s) found: {set(duplicates)}")

        # 3c. Unique zone IDs
        zone_ids = [z.id for z in self.zones]
        if len(zone_ids) != len(set(zone_ids)):
            duplicates = [zid for zid in zone_ids if zone_ids.count(zid) > 1]
            raise ValueError(f"Duplicate zone ID(s) found: {set(duplicates)}")

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


SENSITIVE_KEYS: set[str] = {
    "password",
    "auth_token",
    "refresh_token",
    "api_key",
    "totp_key",
    "key_id",
    "client_secret",
}


def mask_secret(val: str | None, prefix: int = 2, suffix: int = 2) -> str | None:
    """Mask secret token or password with asterisk characters."""
    if val is None:
        return None
    if not val:
        return ""
    if len(val) <= prefix + suffix:
        return "******"
    return f"{val[:prefix]}***{val[-suffix:]}"


def mask_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively traverse a dictionary and mask sensitive secrets."""
    masked: dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(val, dict):
            masked[key] = mask_config_dict(val)
        elif isinstance(val, list):
            masked[key] = [
                mask_config_dict(item) if isinstance(item, dict) else item for item in val
            ]
        elif key.lower() == "password":
            masked[key] = "******" if val else val
        elif key.lower() in SENSITIVE_KEYS:
            masked[key] = mask_secret(str(val), prefix=2, suffix=2) if val is not None else None
        else:
            masked[key] = val
    return masked
