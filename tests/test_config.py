"""Unit tests for configuration schemas and YAML loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lookout.config.loader import (
    load_config,
    load_config_from_dict,
    load_config_from_yaml,
)
from lookout.config.schema import (
    CameraConfig,
    NotificationTargetConfig,
)
from lookout.domain.models import ThreatLevel

SAMPLE_VALID_YAML = """
system_name: "Test Lookout"
residents:
  - id: "res-1"
    name: "Alice"
    notification_targets:
      - "webhook-alert"
    enrolled_faces:
      - "/faces/alice.jpg"
pets:
  - id: "pet-1"
    name: "Rex"
    species: "dog"
cameras:
  - id: "cam-front"
    name: "Front Door"
    stream_url: "rtsp://camera.local:554/live"
    enabled: true
    location: "Porch"
    sensitivity: 0.8
    record_duration: 12.0
notification_targets:
  - name: "webhook-alert"
    target_type: "webhook"
    destination: "https://notify.example.com/alerts"
    min_threat_level: "low"
    enabled: true
storage:
  base_path: "/tmp/lookout"
  retention_days: 14
  max_storage_gb: 50.0
"""


def test_valid_config_loads() -> None:
    """TR-5: Verify valid configuration parses and populates camera collection."""
    config = load_config_from_yaml(SAMPLE_VALID_YAML)
    assert len(config.cameras) == 1
    assert config.system_name == "Test Lookout"
    assert config.cameras[0].id == "cam-front"
    assert config.cameras[0].stream_url == "rtsp://camera.local:554/live"


def test_duplicate_camera_id_fails() -> None:
    """TR-6: Verify duplicate camera identifiers are rejected with validation error."""
    invalid_data = {
        "cameras": [
            {"id": "cam-duplicate", "name": "Camera 1", "stream_url": "rtsp://cam1.local/live"},
            {"id": "cam-duplicate", "name": "Camera 2", "stream_url": "rtsp://cam2.local/live"},
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        load_config_from_dict(invalid_data)
    assert exc_info.value is not None


def test_resident_and_pet_validation() -> None:
    """TR-7: Verify resident and pet entities validate with correct attributes."""
    config = load_config_from_yaml(SAMPLE_VALID_YAML)
    assert config.residents[0].name == "Alice"
    assert config.residents[0].id == "res-1"
    assert config.pets[0].name == "Rex"
    assert config.pets[0].species == "dog"


def test_load_example_config_file() -> None:
    """Verify reference config.example.yaml file loads and passes all validation."""
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    config = load_config(example_path)
    assert len(config.cameras) == 1
    assert len(config.residents) == 1
    assert len(config.pets) == 1
    assert len(config.notification_targets) == 1
    assert config.residents[0].name == "Alice"
    assert config.cameras[0].id == "front-porch"


def test_duplicate_resident_id_fails() -> None:
    """Verify duplicate resident identifiers fail validation."""
    data = {
        "residents": [
            {"id": "res-dup", "name": "Alice"},
            {"id": "res-dup", "name": "Bob"},
        ]
    }
    with pytest.raises(ValidationError):
        load_config_from_dict(data)


def test_duplicate_pet_id_fails() -> None:
    """Verify duplicate pet identifiers fail validation."""
    data = {
        "pets": [
            {"id": "pet-dup", "name": "Dog 1"},
            {"id": "pet-dup", "name": "Dog 2"},
        ]
    }
    with pytest.raises(ValidationError):
        load_config_from_dict(data)


def test_duplicate_notification_target_fails() -> None:
    """Verify duplicate notification target names fail validation."""
    data = {
        "notification_targets": [
            {"name": "ntfy", "target_type": "ntfy", "destination": "topic1"},
            {"name": "ntfy", "target_type": "ntfy", "destination": "topic2"},
        ]
    }
    with pytest.raises(ValidationError):
        load_config_from_dict(data)


def test_resident_references_undefined_target_fails() -> None:
    """Verify resident notification target must exist in notification_targets."""
    data = {
        "residents": [{"id": "res-1", "name": "Alice", "notification_targets": ["unknown-target"]}],
        "notification_targets": [
            {"name": "valid-target", "target_type": "email", "destination": "a@b.com"}
        ],
    }
    with pytest.raises(ValidationError):
        load_config_from_dict(data)


def test_invalid_stream_url_scheme() -> None:
    """Verify unsupported camera stream schemes are rejected."""
    with pytest.raises(ValidationError):
        CameraConfig(id="c1", name="Cam", stream_url="ftp://camera.local/stream")


def test_invalid_stream_url_missing_host() -> None:
    """Verify camera stream url with no netloc or path is rejected."""
    with pytest.raises(ValidationError):
        CameraConfig(id="c1", name="Cam", stream_url="rtsp://")


def test_empty_yaml_produces_defaults() -> None:
    """Verify empty YAML returns default config."""
    config = load_config_from_yaml("")
    assert config.system_name == "Lookout"
    assert config.cameras == []
    assert config.residents == []
    assert config.storage.base_path == "./data"


def test_non_mapping_yaml_raises_value_error() -> None:
    """Verify non-mapping top-level YAML raises ValueError."""
    with pytest.raises(ValueError):
        load_config_from_yaml("- item 1\n- item 2")


def test_non_dict_config_raises_value_error() -> None:
    """Verify non-dict data passed to load_config_from_dict raises ValueError."""
    with pytest.raises(ValueError):
        load_config_from_dict(["not", "a", "dict"])  # type: ignore[arg-type]


def test_file_not_found_raises() -> None:
    """Verify non-existent config file path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("/non/existent/path/config.yaml")


def test_notification_target_threat_level_parsing() -> None:
    """Verify min_threat_level parses from string, int, and enum."""
    t1 = NotificationTargetConfig(
        name="t1",
        target_type="webhook",
        destination="http://localhost",
        min_threat_level="high",  # type: ignore[arg-type]
    )
    assert t1.min_threat_level == ThreatLevel.HIGH

    t2 = NotificationTargetConfig(
        name="t2",
        target_type="webhook",
        destination="http://localhost",
        min_threat_level=3,  # type: ignore[arg-type]
    )
    assert t2.min_threat_level == ThreatLevel.HIGH

    t3 = NotificationTargetConfig(
        name="t3",
        target_type="webhook",
        destination="http://localhost",
        min_threat_level=ThreatLevel.CRITICAL,
    )
    assert t3.min_threat_level == ThreatLevel.CRITICAL

    with pytest.raises(ValidationError):
        NotificationTargetConfig(
            name="t4",
            target_type="webhook",
            destination="http://localhost",
            min_threat_level="invalid_level",  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        NotificationTargetConfig(
            name="t5",
            target_type="webhook",
            destination="http://localhost",
            min_threat_level=12.34,  # type: ignore[arg-type]
        )


def test_storage_config_sqlite_settings() -> None:
    """TR-27: Verify storage config supports SQLite path, WAL mode, and retention parameters."""
    config = load_config_from_yaml(SAMPLE_VALID_YAML)
    assert config.storage.wal_mode is True
    assert config.storage.database_path == "./data/lookout.db"
    assert config.storage.routine_retention_days == 7
    assert config.storage.critical_retention_days == 30
    assert config.storage.busy_timeout_ms == 5000


def test_transcode_optimization_config_parsing() -> None:
    """TR-19: Verify transcode optimization configuration fields parse correctly."""
    yaml_content = """
    transcode:
      ffmpeg_path: "/opt/bin/ffmpeg"
      use_stream_copy: true
      max_threads: 2
      nice_level: 10
      temp_dir: "/dev/shm"
      gif_fps: 6
      gif_width: 320
      gif_duration: 2.5
      thumbnail_count: 5
    """
    config = load_config_from_yaml(yaml_content)
    assert config.transcode.use_stream_copy is True
    assert config.transcode.max_threads == 2
    assert config.transcode.nice_level == 10
    assert config.transcode.temp_dir == "/dev/shm"


def test_ring_stream_url_scheme_validation() -> None:
    """TR-20: Verify ring:// scheme URLs are permitted for camera stream_url."""
    yaml_content = """
    cameras:
      - id: "ring-doorbell"
        name: "Front Doorbell"
        camera_type: "ring"
        stream_url: "ring://front-doorbell"
    """
    config = load_config_from_yaml(yaml_content)
    assert config.cameras[0].stream_url == "ring://front-doorbell"


def test_ai_inference_config_validation() -> None:
    """TR-30: Validate AI inference configurations for Ollama, OpenCode, and Gemini."""
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    config = load_config(example_path)

    assert config.ollama.model == "moondream"
    assert config.ollama.host == "http://localhost:11434"
    assert config.ollama.timeout_seconds == 5.0
    assert config.opencode.model_go == "deepseek-v4.1-flash"
    assert config.opencode.model_free == "deepseek-v4.1-flash-free"
    assert config.gemini.model == "gemini-3.5-flash-lite"


def test_cascade_tiers_validation() -> None:
    """TR-31: Validate multi-tier cascade configuration and default tier sequence."""
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    config = load_config(example_path)

    assert len(config.cascade.tiers) == 6
    assert config.cascade.tiers == [
        "ollama",
        "opencode_go",
        "gemini",
        "opencode_free",
        "local_edge",
        "offline",
    ]
    assert config.cascade.total_timeout_seconds == 25.0


def test_immich_config_validation() -> None:
    """TR-32: Validate Immich synchronization parameters and inclusion filters."""
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    config = load_config(example_path)

    assert config.immich.sync_interval_hours == 24
    assert config.immich.url == "http://localhost:2283"
    assert "Alice" in config.immich.include_persons
    assert config.face_recognition.similarity_threshold == 0.6
    assert config.audio_classifier.sample_rate == 16000


def test_wyze_config_schema_validation() -> None:
    """TR-14: Validate WyzeConfig schema attributes and defaults."""
    yaml_content = """
    wyze:
      email: "user@wyze.com"
      password: "secretpassword"
      auth_token: "wyze-token-abc"
      enabled: true
    """
    config = load_config_from_yaml(yaml_content)
    assert config.wyze.email == "user@wyze.com" and config.wyze.enabled is True


def test_ring_config_extended_schema_validation() -> None:
    """TR-15: Validate RingConfig extended schema with refresh token and oauth_url."""
    yaml_content = """
    ring:
      refresh_token: "token-123"
      oauth_url: "https://oauth.ring.com"
      enabled: true
    """
    config = load_config_from_yaml(yaml_content)
    assert (
        config.ring.refresh_token == "token-123"
        and config.ring.oauth_url == "https://oauth.ring.com"
    )


def test_storage_config_extended_schema_validation() -> None:
    """TR-6: Validate extended storage quota configuration and defaults."""
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    config = load_config(example_path)
    assert (
        config.storage.max_storage_percent == 80.0
        and config.storage.retention_mode == "fifo_oldest_first"
    )


def test_storage_config_archive_settings_validation() -> None:
    """TR-7: Validate archive settings schema and network share parameters."""
    yaml_content = """
    storage:
      archive_enabled: true
      archive_path: "/mnt/network_archive"
      archive_max_storage_gb: 500.0
      archive_max_storage_percent: 85.0
    """
    config = load_config_from_yaml(yaml_content)
    assert config.storage.archive_enabled is True and config.storage.archive_path is not None
