"""Constants for the Lookout Camera Intelligence Home Assistant integration."""

from __future__ import annotations

DOMAIN = "lookout"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000
DEFAULT_NAME = "Lookout"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"

PLATFORMS = ["camera", "binary_sensor", "sensor", "select"]

ATTR_CAMERA_ID = "camera_id"
ATTR_THREAT_LEVEL = "threat_level"
ATTR_CONFIDENCE = "confidence"
ATTR_LATENCY_MS = "latency_ms"
ATTR_MATCHED_ENTITIES = "matched_entities"
ATTR_SUMMARY = "summary"
