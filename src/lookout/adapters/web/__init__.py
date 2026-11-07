"""Web adapter package for Lookout."""

from lookout.adapters.web.app import create_app
from lookout.adapters.web.broadcast import SSEBroadcastHub

__all__ = [
    "SSEBroadcastHub",
    "create_app",
]
