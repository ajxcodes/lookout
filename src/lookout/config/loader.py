"""Configuration loader functions for YAML files and dictionaries."""

from pathlib import Path
from typing import Any

import yaml

from lookout.config.schema import LookoutConfig


def load_config_from_dict(data: dict[str, Any]) -> LookoutConfig:
    """Validate and instantiate LookoutConfig from a raw dictionary."""
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping, got {type(data).__name__}")
    return LookoutConfig.model_validate(data)


def load_config_from_yaml(content: str) -> LookoutConfig:
    """Parse YAML string content and validate into LookoutConfig."""
    data = yaml.safe_load(content)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(
            f"YAML configuration must define a top-level mapping, got {type(data).__name__}"
        )
    return load_config_from_dict(data)


def load_config(path: str | Path) -> LookoutConfig:
    """Load and validate configuration from a YAML file on disk."""
    config_file = Path(path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    content = config_file.read_text(encoding="utf-8")
    return load_config_from_yaml(content)
