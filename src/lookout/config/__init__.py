"""Configuration schemas and loaders for Lookout."""

from lookout.config.loader import (
    load_config,
    load_config_from_dict,
    load_config_from_yaml,
)
from lookout.config.schema import (
    CameraConfig,
    Go2RtcConfig,
    LookoutConfig,
    NotificationTargetConfig,
    PetConfig,
    ResidentConfig,
    RingConfig,
    StorageConfig,
    TranscodeConfig,
)

__all__ = [
    "CameraConfig",
    "Go2RtcConfig",
    "LookoutConfig",
    "NotificationTargetConfig",
    "PetConfig",
    "ResidentConfig",
    "RingConfig",
    "StorageConfig",
    "TranscodeConfig",
    "load_config",
    "load_config_from_dict",
    "load_config_from_yaml",
]
