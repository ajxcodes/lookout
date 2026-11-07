"""Compatibility shim for ntfy dispatcher imports."""

from lookout.adapters.ntfy import (
    NtfyAdapter,
    NtfyDispatcher,
    NtfyNotificationAdapter,
)

__all__ = ["NtfyAdapter", "NtfyDispatcher", "NtfyNotificationAdapter"]
