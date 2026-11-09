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
    c_type = (cam.camera_type or "generic").lower()
    provider_badge = "generic"
    if "ring" in c_type:
        provider_badge = "ring"
    elif "wyze" in c_type:
        provider_badge = "wyze"

    # Battery/on-demand feeds (e.g. Ring) vs continuous WebRTC feeds (e.g. Wyze/RTSP)
    stream_mode = "on_demand" if provider_badge == "ring" else "live_rtc"
    icon = infer_camera_icon(cam.name or cam.location)

    return {
        "id": cam.id,
        "name": cam.name,
        "stream_url": cam.stream_url,
        "camera_type": cam.camera_type,
        "enabled": cam.enabled,
        "location": cam.location,
        "icon": icon,
        "provider_badge": provider_badge,
        "stream_mode": stream_mode,
        "event_count": event_count,
    }
