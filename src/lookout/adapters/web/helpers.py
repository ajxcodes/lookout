"""Web adapter presentation helpers, icon inference, and metadata extractors."""

from __future__ import annotations

from typing import Any

from lookout.config.schema import CameraConfig


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


def build_camera_card_data(cam: CameraConfig, event_count: int = 0) -> dict[str, Any]:
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

    return {
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
    }
