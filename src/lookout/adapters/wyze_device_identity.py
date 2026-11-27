"""Shared Wyze device-identity resolution for the motion-tag lanes.

The cloud lane and the HL/Hualai lane must agree on a camera's ``(device_id,
model)`` pair. Resolution order is: an injected ``device_resolver``, then the
native adapter's ``resolve_device_identity``, then the configured camera itself.
A configured ``native_model`` always wins over the ``wyze://`` stream-URL
``model`` query so discovery-backfilled identity does not depend on the stream
URL fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class WyzeDeviceIdentity:
    """Resolved Wyze device id (MAC) and product model."""

    device_id: str
    model: str


def identity_from_camera(camera: Any) -> WyzeDeviceIdentity | None:
    """Derive identity from a configured camera, or ``None`` when incomplete.

    The camera must look like a Wyze camera (``native_provider == "wyze"`` or a
    ``wyze://`` stream URL). ``native_device_id`` / ``native_model`` take
    precedence over the stream-URL ``mac`` / ``model`` query values.
    """
    provider = str(getattr(camera, "native_provider", "") or "").lower()
    stream_url = str(getattr(camera, "stream_url", "") or "")
    if provider != "wyze" and not stream_url.startswith("wyze://"):
        return None

    device_id = getattr(camera, "native_device_id", None)
    model = getattr(camera, "native_model", None)
    if stream_url.startswith("wyze://"):
        query = parse_qs(urlsplit(stream_url).query)
        if not device_id:
            mac_values = query.get("mac") or []
            if mac_values:
                device_id = mac_values[0]
        if not model:
            model_values = query.get("model") or []
            if model_values:
                model = model_values[0]
    if not device_id or not model:
        return None
    return WyzeDeviceIdentity(str(device_id), str(model))


def iter_cameras(cameras: Any) -> list[Any]:
    """Return cameras from a sequence, mapping, or zero-arg callable source."""
    source = cameras() if callable(cameras) else cameras
    if source is None:
        return []
    if isinstance(source, dict):
        return list(source.values())
    try:
        return list(source)
    except TypeError:
        return []


def _coerce_identity(value: Any) -> WyzeDeviceIdentity | None:
    if isinstance(value, WyzeDeviceIdentity):
        return value
    if value is None:
        return None
    try:
        device_id, model = value
    except (TypeError, ValueError):
        return None
    if not device_id or not model:
        return None
    return WyzeDeviceIdentity(str(device_id), str(model))


def resolve_wyze_device_identity(
    camera_id: str,
    *,
    cameras: Any = (),
    device_resolver: Callable[[str], Any] | None = None,
    native_adapter: Any = None,
) -> WyzeDeviceIdentity | None:
    """Resolve ``camera_id`` to a Wyze identity following the documented order.

    Resolver failures are swallowed: a failed lookup is retryable and must never
    be cached as an unsupported verdict.
    """
    cid = str(camera_id)

    if device_resolver is not None:
        try:
            resolved = _coerce_identity(device_resolver(cid))
        except Exception:  # noqa: BLE001 - failed resolution is retried later
            resolved = None
        if resolved is not None:
            return resolved

    if native_adapter is not None:
        resolver = getattr(native_adapter, "resolve_device_identity", None)
        if callable(resolver):
            try:
                resolved = _coerce_identity(resolver(cid))
            except Exception:  # noqa: BLE001 - failed resolution is retried later
                resolved = None
            if resolved is not None:
                return resolved

    for camera in iter_cameras(cameras):
        if str(getattr(camera, "id", "")) == cid:
            return identity_from_camera(camera)
    return None


__all__ = [
    "WyzeDeviceIdentity",
    "identity_from_camera",
    "iter_cameras",
    "resolve_wyze_device_identity",
]
