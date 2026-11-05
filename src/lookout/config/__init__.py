"""Configuration schemas and loaders for Lookout."""

from lookout.config.loader import (
    load_config,
    load_config_from_dict,
    load_config_from_yaml,
)
from lookout.config.schema import (
    CameraConfig,
    LookoutConfig,
    NotificationTargetConfig,
    PetConfig,
    ResidentConfig,
    StorageConfig,
)

__all__ = [
    "CameraConfig",
    "LookoutConfig",
    "NotificationTargetConfig",
    "PetConfig",
    "ResidentConfig",
    "StorageConfig",
    "load_config",
    "load_config_from_dict",
    "load_config_from_yaml",
]
