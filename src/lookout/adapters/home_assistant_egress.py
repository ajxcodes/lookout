"""Compatibility shim for Home Assistant egress imports."""

from lookout.adapters.home_assistant import (
    HAEgressAdapter,
    HomeAssistantAdapter,
    HomeAssistantEgressAdapter,
)

__all__ = ["HAEgressAdapter", "HomeAssistantAdapter", "HomeAssistantEgressAdapter"]
