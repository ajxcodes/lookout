"""ntfy push notification dispatcher adapter."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from lookout.domain.models import CameraEvent, ThreatLevel
from lookout.ports.egress import NotificationPort

logger = logging.getLogger(__name__)

_DEFAULT_NTFY_SERVER = "https://ntfy.sh"
_DEFAULT_DEEP_LINK_BASE = "http://localhost:8000"

_PRIORITY_MAP: dict[ThreatLevel, str] = {
    ThreatLevel.INFO: "2",
    ThreatLevel.LOW: "3",
    ThreatLevel.MEDIUM: "4",
    ThreatLevel.HIGH: "5",
    ThreatLevel.CRITICAL: "5",
}


def _is_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _basename(uri: str | None) -> str | None:
    if not uri:
        return None
    try:
        path = urlparse(uri).path
        name = path.rsplit("/", 1)[-1]
        return name or None
    except Exception:  # pragma: no cover - defensive
        return None


class NtfyDispatcher(NotificationPort):
    """Dispatcher publishing camera event alerts to an ntfy topic."""

    def __init__(
        self,
        topic: str | None = None,
        *,
        topic_url: str | None = None,
        url: str | None = None,
        destination: str | None = None,
        server: str | None = None,
        ntfy_url: str | None = None,
        ntfy_server: str | None = None,
        endpoint: str | None = None,
        target: str | None = None,
        channel: str | None = None,
        name: str | None = None,
        base_url: str | None = None,
        camera_base_url: str | None = None,
        deep_link_base_url: str | None = None,
        event_base_url: str | None = None,
        app_base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        http_client: httpx.AsyncClient | None = None,
        async_client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        if topic is None:
            topic = kwargs.pop("topic", None) or kwargs.pop("topic_name", None)
        if topic_url is None:
            topic_url = kwargs.pop("topic_url", None)
        if url is None:
            url = kwargs.pop("url", None)
        if destination is None:
            destination = kwargs.pop("destination", None)
        if server is None:
            server = kwargs.pop("server", None)
        if ntfy_url is None:
            ntfy_url = kwargs.pop("ntfy_url", None)
        if ntfy_server is None:
            ntfy_server = kwargs.pop("ntfy_server", None)
        if endpoint is None:
            endpoint = kwargs.pop("endpoint", None)
        if target is None:
            target = kwargs.pop("target", None)
        if channel is None:
            channel = kwargs.pop("channel", None)
        if name is None:
            name = kwargs.pop("name", None)
        if base_url is None:
            base_url = kwargs.pop("base_url", None)
        if camera_base_url is None:
            camera_base_url = kwargs.pop("camera_base_url", None)
        if deep_link_base_url is None:
            deep_link_base_url = kwargs.pop("deep_link_base_url", None)
        if event_base_url is None:
            event_base_url = kwargs.pop("event_base_url", None)
        if app_base_url is None:
            app_base_url = kwargs.pop("app_base_url", None)
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

        server_candidate = (
            server
            or ntfy_url
            or ntfy_server
            or endpoint
            or (url if (url and not topic and not topic_url and not destination) else None)
            or _DEFAULT_NTFY_SERVER
        )

        resolved_topic_url: str | None = None
        if topic_url and _is_url(topic_url):
            resolved_topic_url = topic_url
        elif destination and _is_url(destination):
            resolved_topic_url = destination
        elif url and _is_url(url) and not (topic or channel or name or target or destination):
            resolved_topic_url = url
        elif topic or channel or name or target or destination:
            chosen = topic or channel or name or destination or target
            if _is_url(chosen):
                resolved_topic_url = chosen
            else:
                resolved_topic_url = f"{str(server_candidate).rstrip('/')}/{chosen}"
        if resolved_topic_url is None:
            resolved_topic_url = str(server_candidate)
        if not _is_url(resolved_topic_url):
            resolved_topic_url = f"{_DEFAULT_NTFY_SERVER}/{resolved_topic_url}"

        self.topic_url = resolved_topic_url
        self.server_url = str(server_candidate).rstrip("/")
        self._client = client

        deep_base = (
            camera_base_url
            or deep_link_base_url
            or event_base_url
            or app_base_url
            or os.environ.get("LOOKOUT_BASE_URL")
            or base_url
            or _DEFAULT_DEEP_LINK_BASE
        )
        # Avoid using ntfy server as deep link base if base_url was ntfy server.
        if (
            deep_base.rstrip("/").endswith(_DEFAULT_NTFY_SERVER.rstrip("/"))
            or "ntfy.sh" in deep_base
        ):
            deep_base = _DEFAULT_DEEP_LINK_BASE
        self.deep_link_base_url = str(deep_base).rstrip("/")
        self.url = self.topic_url
        self.topic = topic if topic is not None else (channel or name or target or destination)
        self.server = self.server_url
        self.client = self._client

    async def send_notification(
        self, event: CameraEvent, target: str | None = None, message: str | None = None
    ) -> bool:
        url = self.topic_url
        if target is not None:
            if _is_url(target):
                url = target
            elif target in ("", "default"):
                url = self.topic_url
            else:
                configured = (
                    bool(self.topic_url)
                    and not self.topic_url.rstrip("/").endswith(_DEFAULT_NTFY_SERVER)
                    and not self.topic_url.rstrip("/").endswith("/lookout")
                )
                url = self.topic_url if configured else f"{_DEFAULT_NTFY_SERVER}/{target}"

        priority = _PRIORITY_MAP.get(event.threat_level, "3")
        tags = f"{event.category.value},{event.camera_id}"
        body = message if message is not None else f"[{event.threat_level.name}] {event.summary}"

        headers: dict[str, str] = {
            "Title": f"Lookout: {event.summary}",
            "Priority": priority,
            "Tags": tags,
            "Markdown": "yes",
        }

        media_uri = event.media_uri or event.snapshot_uri
        if media_uri:
            headers["Attach"] = media_uri
            filename = _basename(media_uri)
            if filename:
                headers["Filename"] = filename

        event_url = f"{self.deep_link_base_url}/events/{event.id}"
        stream_url = f"{self.deep_link_base_url}/cameras/{event.camera_id}/live"
        actions = f"view, View Event, {event_url}; view, Live Stream, {stream_url}"
        headers["Actions"] = actions

        try:
            if self._client is not None:
                resp = await self._client.post(url, content=body, headers=headers)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, content=body, headers=headers)
            return bool(resp.status_code < 400)
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning("ntfy dispatch failed: %s", exc)
            return False


NtfyAdapter = NtfyDispatcher
NtfyNotificationAdapter = NtfyDispatcher
