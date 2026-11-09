"""ntfy push notification dispatcher adapter."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
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

_THREAT_EMOJIS: dict[ThreatLevel, str] = {
    ThreatLevel.CRITICAL: "🚨",
    ThreatLevel.HIGH: "⚠️",
    ThreatLevel.MEDIUM: "🟡",
    ThreatLevel.LOW: "ℹ️",
    ThreatLevel.INFO: "🟢",
}

_OBJECT_ICONS: dict[str, str] = {
    "person": "👤",
    "resident": "👤",
    "visitor": "👤",
    "unknown_visitor": "👤",
    "verified_resident": "👤",
    "vehicle": "🚗",
    "car": "🚗",
    "truck": "🚗",
    "suv": "🚗",
    "bicycle": "🚲",
    "dog": "🐕",
    "cat": "🐈",
    "pet": "🐾",
    "known_pet": "🐾",
    "pet_elimination": "💩",
    "package": "📦",
    "box": "📦",
    "package_event": "📦",
    "courier_delivery": "🚚",
    "food_delivery": "🛵",
    "acoustic_anomaly": "🔊",
    "alarm": "🚨",
    "ding": "🔔",
    "doorbell": "🔔",
    "false_positive": "🍃",
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


def _rfc2047_encode(text: str) -> str:
    if text.isascii():
        return text
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{encoded}?="


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
        media_base_url: str | None = None,
        use_companion_app: bool = False,
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
        self._owned_client: httpx.AsyncClient | None = None

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
        self.media_base_url = str(
            media_base_url or os.environ.get("LOOKOUT_BASE_URL") or ""
        ).rstrip("/")
        self.use_companion_app = bool(use_companion_app or kwargs.pop("use_companion_app", False))
        self.url = self.topic_url
        self.topic = topic if topic is not None else (channel or name or target or destination)
        self.server = self.server_url
        self.client = self._client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient()
        return self._owned_client

    async def aclose(self) -> None:
        """Close the owned HTTP client, if any.

        Injected clients (``self._client``) are left untouched.
        """
        client = self._owned_client
        self._owned_client = None
        if client is None:
            return
        aclose_method = getattr(client, "aclose", None)
        if callable(aclose_method):
            result = aclose_method()
            if inspect.isawaitable(result):
                await result
            return
        close_method = getattr(client, "close", None)
        if callable(close_method):
            result = close_method()
            if inspect.isawaitable(result):
                await result

    def close(self) -> None:
        """Synchronously close the owned HTTP client when possible."""
        client = self._owned_client
        self._owned_client = None
        if client is None:
            return
        close_method = getattr(client, "close", None)
        if callable(close_method):
            try:
                result = close_method()
                if inspect.isawaitable(result):
                    self._schedule_close(result)
            except Exception:  # pragma: no cover - defensive
                pass
            return
        aclose_method = getattr(client, "aclose", None)
        if callable(aclose_method):
            try:
                result = aclose_method()
                if inspect.isawaitable(result):
                    self._schedule_close(result)
            except Exception:  # pragma: no cover - defensive
                pass

    def _schedule_close(self, awaitable: Any) -> None:
        try:
            asyncio.run(awaitable)
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                return
            if loop.is_running():
                loop.create_task(awaitable)

    async def __aenter__(self) -> NtfyDispatcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.aclose()

    def __enter__(self) -> NtfyDispatcher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

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
        threat_emoji = _THREAT_EMOJIS.get(event.threat_level, "ℹ️")
        cam_name = event.camera_id.replace("_", " ").title()

        found_labels: list[str] = []
        if event.metrics and event.metrics.bounding_boxes:
            for b in event.metrics.bounding_boxes:
                lbl = b.label.lower()
                if lbl not in found_labels:
                    found_labels.append(lbl)
        if event.matched_entities:
            for ent in event.matched_entities:
                if ent.lower() not in [x.lower() for x in found_labels]:
                    found_labels.append(ent)

        if found_labels:
            rendered: list[str] = []
            for lbl in found_labels[:3]:
                icon = _OBJECT_ICONS.get(lbl.lower(), "")
                prefix = f"{icon} " if icon else ""
                rendered.append(f"{prefix}{lbl.title()}")
            objects_str = " & ".join(rendered)
        else:
            cat_key = (
                event.category.value if hasattr(event.category, "value") else str(event.category)
            )
            icon = _OBJECT_ICONS.get(cat_key.lower(), "")
            prefix = f"{icon} " if icon else ""
            objects_str = f"{prefix}{cat_key.replace('_', ' ').title()}"

        title = f"{threat_emoji} [{event.threat_level.name}] {objects_str} on {cam_name}"

        tags_list = [event.category.value, event.camera_id]
        if event.zone_id:
            tags_list.append(event.zone_id)
        tags = ",".join(tags_list)

        if message is not None:
            body = message
        elif event.is_pet_elimination() or str(event.category) in (
            "pet_elimination",
            "EventCategory.PET_ELIMINATION",
        ):
            pet_name = (
                (event.matched_entities and event.matched_entities[0]) or event.pet_id or "Mochi"
            )
            zone_label = (
                "Backyard (Lawn)"
                if event.zone_id in ("lawn", "Backyard (Lawn)")
                else (event.zone_id or "yard")
            )
            body = f"💩 {pet_name} pooped in {zone_label}"
        else:
            body_parts = [event.summary]
            if event.matched_entities:
                body_parts.append(f"• **Identified**: {', '.join(event.matched_entities)}")
            if event.zone_id:
                body_parts.append(f"• **Zone**: {event.zone_id}")
            if event.metrics and event.metrics.confidence > 0:
                body_parts.append(f"• **Confidence**: {int(event.metrics.confidence * 100)}%")
            body = "\n".join(body_parts)

        headers: dict[str, str] = {
            "Title": _rfc2047_encode(title),
            "Priority": priority,
            "Tags": tags,
            "Markdown": "yes",
            "X-Sequence-ID": event.id,
            "Sequence-ID": event.id,
        }

        gif_uri = getattr(event, "gif_url", None) or (
            event.metadata.get("gif_uri") if event.metadata else None
        )
        media_uri = gif_uri or event.snapshot_uri or event.media_uri
        if (
            media_uri
            and not _is_url(media_uri)
            and media_uri.startswith("/")
            and self.media_base_url
        ):
            media_uri = f"{self.media_base_url}{media_uri}"
        if media_uri and _is_url(media_uri):
            headers["Attach"] = media_uri
            filename = _basename(media_uri)
            if filename:
                headers["Filename"] = filename

        if self.use_companion_app:
            ha_path = (
                "cameras/events"
                if "/cameras/events" in self.deep_link_base_url
                else "lookout/viewer"
            )
            ha_app_event = f"homeassistant://navigate/{ha_path}?camera_id={event.camera_id}&event_id={event.id}"
            ha_app_live = (
                f"homeassistant://navigate/{ha_path}?camera_id={event.camera_id}&live=true"
            )
            browser_base = self.media_base_url or self.deep_link_base_url.rstrip("/")
            browser_url = (
                f"{browser_base}/?camera_id={event.camera_id}&event_id={event.id}"
                if self.media_base_url
                else f"{self.deep_link_base_url}?camera_id={event.camera_id}&event_id={event.id}"
            )
            actions = (
                f"view, View in App, {ha_app_event}; "
                f"view, Live Stream, {ha_app_live}; "
                f"view, Web Browser, {browser_url}"
            )
            headers["Actions"] = actions
            headers["Click"] = ha_app_event
        else:
            base_clean = self.deep_link_base_url.rstrip("/")
            if "?" in self.deep_link_base_url:
                sep = "&" if "?" in self.deep_link_base_url else "?"
                event_url = (
                    f"{self.deep_link_base_url}{sep}camera_id={event.camera_id}&event_id={event.id}"
                )
                stream_url = f"{self.deep_link_base_url}{sep}camera_id={event.camera_id}&live=true"
            elif base_clean.endswith(("/lookout", "/lookout/viewer", "/cameras/events")):
                # Home Assistant panel_iframe / Lovelace view path
                event_url = (
                    f"{self.deep_link_base_url}?camera_id={event.camera_id}&event_id={event.id}"
                )
                stream_url = f"{self.deep_link_base_url}?camera_id={event.camera_id}&live=true"
            else:
                event_url = f"{self.deep_link_base_url}/events/{event.id}"
                stream_url = f"{self.deep_link_base_url}/cameras/{event.camera_id}/live"
            actions = f"view, View Event, {event_url}; view, Live Stream, {stream_url}"
            headers["Actions"] = actions
            headers["Click"] = event_url

        try:
            client = self._get_client()
            resp = await client.post(url, content=body, headers=headers)
            return bool(resp.status_code < 400)
        except Exception as exc:  # pragma: no cover - network failures
            logger.warning("ntfy dispatch failed: %s", exc)
            return False


NtfyAdapter = NtfyDispatcher
NtfyNotificationAdapter = NtfyDispatcher
