"""Web adapter presentation helpers, icon inference, and metadata extractors."""

from __future__ import annotations

from typing import Any

from lookout.config.schema import CameraConfig
from lookout.domain.models import ThreatLevel


def infer_camera_icon(name_or_location: str | None) -> str:
    """Infer semantic emoji icon from camera name or location description."""
    if not name_or_location:
        return "📹"
    s = name_or_location.lower()
    if "front door" in s or "door" in s or "entry" in s:
        return "🚪"
    if "backyard" in s or "yard" in s or "garden" in s or "tree" in s:
        return "🌲"
    if "crate" in s or "kennel" in s or "mochi crate" in s:
        return "🐾"
    if "pan" in s or "dog" in s or "cat" in s or "pup" in s:
        return "🐶"
    if "office" in s or "desk" in s or "work" in s:
        return "💼"
    if "driveway" in s or "garage" in s or "car" in s:
        return "🚗"
    if "living" in s or "couch" in s or "lounge" in s:
        return "🛋️"
    if "porch" in s or "patio" in s or "deck" in s:
        return "🏡"
    return "📹"


def build_camera_card_data(
    cam: CameraConfig, event_count: int = 0, *, auth_required: bool = False
) -> dict[str, Any]:
    """Generate structured camera card metadata with provider and stream badges."""
    provider_candidate = cam.provider or cam.camera_type
    s_raw = f"{cam.id or ''} {cam.name or ''} {cam.stream_url or ''}".lower()
    s = s_raw.replace("-", "_")

    if provider_candidate and provider_candidate.lower() not in ("generic", ""):
        prov = provider_candidate.lower()
        if "ring" in prov:
            provider_badge = "ring"
            inferred_stream_mode = "on_demand"
        elif "wyze" in prov:
            provider_badge = "wyze"
            inferred_stream_mode = "live_rtc"
        else:
            provider_badge = prov
            inferred_stream_mode = "live_rtc"
    else:
        # Auto-infer from cam.id, cam.name, or cam.stream_url
        if "front_door" in s or "backyard" in s or "ring" in s:
            provider_badge = "ring"
            inferred_stream_mode = "on_demand"
        elif (
            "mochi_crate_cam" in s
            or "mochi_pan_cam" in s
            or "office_cam" in s
            or "wyze" in s
            or "pan" in s
            or "crate" in s
        ):
            provider_badge = "wyze"
            inferred_stream_mode = "live_rtc"
        else:
            provider_badge = "generic"
            inferred_stream_mode = "live_rtc"

    stream_mode = cam.stream_mode or inferred_stream_mode
    icon = infer_camera_icon(cam.name or cam.location)
    timestamp_overlay = (
        cam.timestamp_overlay
        if cam.timestamp_overlay is not None
        else (provider_badge == "ring" or "ring" in (cam.provider or "").lower())
    )

    min_threat = getattr(cam, "notification_min_threat", None)
    threat_name: str | None = None
    if isinstance(min_threat, ThreatLevel):
        threat_name = min_threat.name
    elif isinstance(min_threat, int):
        threat_name = ThreatLevel(min_threat).name
    elif isinstance(min_threat, str):
        threat_name = min_threat

    card: dict[str, Any] = {
        "id": cam.id,
        "name": cam.name,
        "stream_url": cam.stream_url,
        "camera_type": cam.camera_type,
        "provider": cam.provider,
        "enabled": cam.enabled,
        "location": cam.location,
        "icon": icon,
        "provider_badge": provider_badge,
        "stream_mode": stream_mode,
        "event_count": event_count,
        "timestamp_overlay": timestamp_overlay,
        "ptz_enabled": getattr(cam, "ptz_enabled", False),
        "ptz_protocol": getattr(cam, "ptz_protocol", "none"),
        "ptz_max_speed": getattr(cam, "ptz_max_speed", 0.5),
        "motion_tag_enabled": getattr(cam, "motion_tag_enabled", None),
        "native_provider": getattr(cam, "native_provider", None),
        "auth_required": bool(auth_required),
        "live_stream_url": getattr(cam, "live_stream_url", None),
        "live_source": getattr(cam, "live_stream_url", None),
        "has_live_stream": bool(getattr(cam, "live_stream_url", None)),
        "has_live_source": bool(getattr(cam, "live_stream_url", None)),
        "notifications_enabled": getattr(cam, "notifications_enabled", True),
        "notifications_muted": getattr(cam, "notifications_muted", False),
        "notification_min_threat": threat_name,
        "notification_event_types": list(
            getattr(cam, "notification_event_types", ["motion", "sound", "person", "ding"])
        ),
        "notify_pass1": getattr(cam, "notify_pass1", True),
        "notify_pass2": getattr(cam, "notify_pass2", True),
        "ha_enabled": getattr(cam, "ha_enabled", True),
        "ha_entity_name": getattr(cam, "ha_entity_name", None),
        "ha_unique_id": getattr(cam, "ha_unique_id", None),
        "ha_device_class": getattr(cam, "ha_device_class", None),
        "ha_icon": getattr(cam, "ha_icon", None),
        "ha_event_types": list(
            getattr(cam, "ha_event_types", ["motion", "person", "sound", "ding"])
        ),
    }
    # Wyze grid tiles use the base id (the backend resolves it to the reliable
    # ``sd`` stream); the focus view uses this variant to retry ``hd``.
    if provider_badge == "wyze" and cam.id:
        card["focus_stream_id"] = f"{cam.id}_hd"
    return card
