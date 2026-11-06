"""Mock notification adapter recording dispatched alerts in memory."""

from typing import Any

from lookout.domain.models import CameraEvent
from lookout.ports.egress import NotificationPort


class MockNotificationAdapter(NotificationPort):
    """Notification adapter recording sent messages for test assertions."""

    def __init__(self) -> None:
        self.sent_notifications: list[dict[str, Any]] = []

    async def send_notification(
        self, event: CameraEvent, target: str, message: str | None = None
    ) -> bool:
        """Record dispatched notification in sent list."""
        self.sent_notifications.append(
            {
                "event": event,
                "target": target,
                "message": message or f"[{event.threat_level.name}] {event.summary}",
            }
        )
        return True
