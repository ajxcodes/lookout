"""Home Assistant egress adapter (event bus + logbook)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from lookout.domain.models import CameraEvent
from lookout.ports.egress import NotificationPort

logger = logging.getLogger(__name__)

_DEFAULT_EVENT_TYPE = "sentinel_camera_event"
_ALTERNATE_EVENT_TYPE = "lookout_camera_event"


def _is_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


class HomeAssistantEgressAdapter(NotificationPort):
    """Adapter posting camera events to Home Assistant REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        url: str | None = None,
        ha_url: str | None = None,
        home_assistant_url: str | None = None,
        hass_url: str | None = None,
        endpoint: str | None = None,
        destination: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        access_token: str | None = None,
        auth_token: str | None = None,
        password: str | None = None,
        client: Any = None,
        http_client: Any = None,
        async_client: Any = None,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        logbook: bool = True,
        **kwargs: Any,
    ) -> None:
        if base_url is None:
            base_url = kwargs.pop("base_url", None) or kwargs.pop("base", None)
        if url is None:
            url = kwargs.pop("url", None)
        if ha_url is None:
            ha_url = kwargs.pop("ha_url", None)
        if home_assistant_url is None:
            home_assistant_url = kwargs.pop("home_assistant_url", None)
        if hass_url is None:
            hass_url = kwargs.pop("hass_url", None)
        if endpoint is None:
            endpoint = kwargs.pop("endpoint", None)
        if destination is None:
            destination = kwargs.pop("destination", None)
        if token is None:
            token = (
                kwargs.pop("token", None)
                or kwargs.pop("api_token", None)
                or kwargs.pop("access_token", None)
            )
        if api_key is None:
            api_key = kwargs.pop("api_key", None)
        if access_token is None:
            access_token = kwargs.pop("access_token", None)
        if auth_token is None:
            auth_token = kwargs.pop("auth_token", None)
        if password is None:
            password = kwargs.pop("password", None)
        if client is None:
            client = (
                kwargs.pop("client", None)
                or kwargs.pop("http_client", None)
                or kwargs.pop("async_client", None)
            )
        if http_client is not None:
            client = http_client
        if async_client is not None:
            client = async_client
        if event_type is None:
            event_type = kwargs.pop("event_type", None)
        if event_types is None:
            event_types = kwargs.pop("event_types", None)
        if kwargs.get("logbook") is not None:
            logbook = kwargs.pop("logbook", logbook)

        resolved_base = (
            base_url
            or url
            or ha_url
            or home_assistant_url
            or hass_url
            or endpoint
            or destination
            or kwargs.pop("base", None)
            or "http://localhost:8123"
        )
        # If a full events URL was passed, trim to base.
        if isinstance(resolved_base, str) and "/api/events/" in resolved_base:
            resolved_base = resolved_base.split("/api/events/")[0]
        if isinstance(resolved_base, str) and "/api/services/" in resolved_base:
            resolved_base = resolved_base.split("/api/services/")[0]
        self.base_url = str(resolved_base).rstrip("/")

        self.token = token or api_key or access_token or auth_token or password
        self._client = client
        self._owned_client: Any = None
        self.event_type = event_type
        self.event_types = event_types
        self.logbook_enabled = bool(logbook)
        self.url = self.base_url
        self.ha_url = self.base_url

        self.event_called = False
        self.logbook_called = False
        self.last_status: bool = False

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient()
        return self._owned_client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _payload(self, event: CameraEvent, message: str | None) -> dict[str, Any]:
        return {
            "id": event.id,
            "camera_id": event.camera_id,
            "category": str(
                event.category.value if hasattr(event.category, "value") else event.category
            ),
            "threat_level": int(event.threat_level),
            "threat_level_name": event.threat_level.name,
            "summary": event.summary,
            "timestamp": (
                event.timestamp.isoformat()
                if hasattr(event.timestamp, "isoformat")
                else str(event.timestamp)
            ),
            "media_uri": event.media_uri,
            "snapshot_uri": event.snapshot_uri,
            "metadata": event.metadata,
            "message": message,
        }

    async def send_notification(
        self, event: CameraEvent, target: str | None = None, message: str | None = None
    ) -> bool:
        body = self._payload(event, message)
        text = message if message is not None else f"[{event.threat_level.name}] {event.summary}"

        # Determine candidate event types.
        if self.event_types:
            candidates = list(self.event_types)
        elif self.event_type:
            candidates = [self.event_type]
        else:
            candidates = [_DEFAULT_EVENT_TYPE, _ALTERNATE_EVENT_TYPE]

        event_ok = False
        for et in candidates:
            url = f"{self.base_url}/api/events/{et}"
            try:
                client = self._get_client()
                resp = await client.post(url, json=body, headers=self._headers())
                if resp.status_code < 400:
                    event_ok = True
                    break
            except Exception as exc:  # pragma: no cover - network failures
                logger.debug("HA event post failed for %s: %s", et, exc)
                continue

        if event_ok:
            self.event_called = True

        # Logbook.
        if self.logbook_enabled:
            log_url = f"{self.base_url}/api/services/logbook/log"
            log_payload = {
                "name": "Lookout",
                "message": text,
                "entity_id": f"camera.{event.camera_id}",
                "domain": "lookout",
            }
            try:
                client = self._get_client()
                resp = await client.post(log_url, json=log_payload, headers=self._headers())
                if resp.status_code < 400:
                    self.logbook_called = True
            except Exception as exc:  # pragma: no cover
                logger.debug("HA logbook post failed: %s", exc)

        self.last_status = bool(event_ok)
        return bool(event_ok)


HomeAssistantAdapter = HomeAssistantEgressAdapter
HAEgressAdapter = HomeAssistantEgressAdapter
