"""HL/Hualai camera-property lane for the Wyze motion-tag toggle.

The APK writes this setting with ``ConnectControl.setCameraProperty`` on the
camera's Hualai/TUTK data channel. Lookout owns no TUTK control channel, so the
lane delegates to an external Wyze bridge that exposes the stable
``motion_tagging`` topic (mapped onto ``K10290GetMotionTagging`` /
``K10292SetMotionTagging``, parameter code ``21``). The bridge is only a
transport boundary: this module depends on ``HlCameraPropertyPort`` and the
shared identity helper, never on a sibling adapter.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from lookout.adapters.wyze_device_identity import (
    iter_cameras,
    resolve_wyze_device_identity,
)
from lookout.domain.models import MotionTagState, MotionTagWriteResult

try:  # pragma: no cover - optional dependency guard
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# Cam v3 (``WYZE_CAKP2JFUS``) and Pan v3 (``HL_PAN3``) are the models verified
# to expose the HL camera property. The original Pan (``WYZECP1_JEF``) does not
# and stays unsupported. Both lists are config-driven.
DEFAULT_HL_MOTION_TAG_MODELS = frozenset({"WYZE_CAKP2JFUS", "HL_PAN3"})
HL_MOTION_TAG_PROPERTY = "motion_tagging"

_HL_ON = 1
_HL_OFF = 2

_PROPERTY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ON_STRINGS = frozenset({"1", "on", "true"})
_OFF_STRINGS = frozenset({"2", "off", "false"})


class HlControlError(RuntimeError):
    """Raised when the HL control transport fails (timeout, HTTP error, bad body)."""

    status_code = 502


class HlControlUnsupported(HlControlError):
    """Raised when the bridge does not know the camera or the property."""


def _extract_property_value(raw: Any, property_name: str) -> Any:
    """Pull a property value out of the bridge's tolerated response shapes."""
    if isinstance(raw, dict):
        for key in (property_name, "value", "data", "result"):
            if key in raw:
                return _extract_property_value(raw[key], property_name)
        if len(raw) == 1:
            return _extract_property_value(next(iter(raw.values())), property_name)
        return None
    if isinstance(raw, list):
        if len(raw) == 1:
            return _extract_property_value(raw[0], property_name)
        return None
    return raw


def decode_motion_tag_value(value: Any) -> bool | None:
    """Decode the HL ``1 = on`` / ``2 = off`` encoding, or ``None`` when unreadable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == _HL_ON:
            return True
        if value == _HL_OFF:
            return False
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ON_STRINGS:
            return True
        if normalized in _OFF_STRINGS:
            return False
    return None


class HualaiBridgePropertyTransport:
    """HTTP transport for the external Wyze bridge camera-property API."""

    def __init__(
        self,
        base_url: str | None,
        timeout: float = 10.0,
        client: Any = None,
    ) -> None:
        if not base_url:
            raise ValueError("HualaiBridgePropertyTransport requires a base_url")
        self.base_url = str(base_url).rstrip("/")
        self._timeout = float(timeout)
        self._client = client

    def _url(self, camera_name: str, property_name: str) -> str:
        if not _PROPERTY_NAME_RE.match(str(property_name)):
            raise ValueError(f"Invalid HL property name {property_name!r}")
        bridge_cam = quote(str(camera_name), safe="")
        return f"{self.base_url}/api/{bridge_cam}/{property_name}"

    async def _request(
        self,
        method: str,
        camera_name: str,
        property_name: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(camera_name, property_name)
        client = self._client
        created = False
        if client is None:
            if httpx is None:  # pragma: no cover - dependency guard
                raise HlControlError("httpx is required for the HL bridge transport")
            client = httpx.AsyncClient(timeout=self._timeout)
            created = True
        try:
            if method == "GET":
                response = await client.get(url)
            else:
                response = await client.post(url, data=data)
        except Exception as exc:  # noqa: BLE001 - mapped to a typed 502 below
            if isinstance(exc, HlControlError):
                raise
            raise HlControlError(f"HL bridge request failed: {exc}") from exc
        finally:
            if created:
                await client.aclose()

        status = int(getattr(response, "status_code", 200))
        if status in (404, 501):
            raise HlControlUnsupported(f"HTTP {status}")
        if status >= 400:
            raise HlControlError(f"HTTP {status}")
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001 - unparseable body is a transport failure
            raise HlControlError("HL bridge returned a non-JSON response") from exc

    async def get_property(self, camera_name: str, property_name: str) -> Any:
        """Read ``GET {base}/api/{camera}/{property}`` and return the raw JSON."""
        return await self._request("GET", camera_name, property_name)

    async def set_property(self, camera_name: str, property_name: str, value: Any) -> Any:
        """Write ``POST {base}/api/{camera}/{property}`` with form ``value=…``."""
        return await self._request("POST", camera_name, property_name, data={"value": value})


class WyzeHlMotionTagAdapter:
    """Camera-aware ``MotionTagPort`` over the HL camera-property lane.

    The lane self-gates on its model allowlist and on a configured transport;
    when either is absent it reports ``supported: false`` without any I/O.
    """

    def __init__(
        self,
        *,
        transport: Any = None,
        cameras: Any = (),
        device_resolver: Any = None,
        native_adapter: Any = None,
        model_allowlist: Any = None,
        property_name: str = HL_MOTION_TAG_PROPERTY,
    ) -> None:
        self.transport = transport
        self._cameras_source = cameras
        self.device_resolver = device_resolver
        self.native_adapter = native_adapter
        self.model_allowlist: set[str] = (
            set(model_allowlist)
            if model_allowlist is not None
            else set(DEFAULT_HL_MOTION_TAG_MODELS)
        )
        self.property_name = property_name

    def _camera(self, camera_id: str) -> Any:
        for camera in iter_cameras(self._cameras_source):
            if str(getattr(camera, "id", "")) == str(camera_id):
                return camera
        return None

    def _target(self, camera_id: str) -> tuple[str, str] | None:
        """Return ``(bridge_name, model)`` or ``None`` when identity is unknown."""
        identity = resolve_wyze_device_identity(
            camera_id,
            cameras=self._cameras_source,
            device_resolver=self.device_resolver,
            native_adapter=self.native_adapter,
        )
        if identity is None:
            return None
        camera = self._camera(camera_id)
        if camera is not None:
            bridge_name = getattr(camera, "ptz_cam_name", None) or getattr(camera, "id", None)
        else:
            bridge_name = None
        return str(bridge_name or camera_id), identity.model

    def _model_supported(self, model: str) -> bool:
        return str(model) in self.model_allowlist

    async def get_motion_tag(self, camera_id: str) -> MotionTagState:
        target = self._target(camera_id)
        if target is None:
            return MotionTagState(supported=False, enabled=None)
        bridge_name, model = target
        if not self._model_supported(model) or self.transport is None:
            return MotionTagState(supported=False, enabled=None)
        try:
            raw = await self.transport.get_property(bridge_name, self.property_name)
        except HlControlUnsupported:
            return MotionTagState(supported=False, enabled=None)
        enabled = decode_motion_tag_value(_extract_property_value(raw, self.property_name))
        if enabled is None:
            return MotionTagState(supported=False, enabled=None)
        return MotionTagState(supported=True, enabled=enabled)

    async def set_motion_tag(self, camera_id: str, enabled: bool) -> MotionTagWriteResult:
        target = self._target(camera_id)
        if target is None:
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        bridge_name, model = target
        if not self._model_supported(model) or self.transport is None:
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        value = _HL_ON if enabled else _HL_OFF
        try:
            await self.transport.set_property(bridge_name, self.property_name, value)
        except HlControlUnsupported:
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        return MotionTagWriteResult(
            supported=True,
            enabled=bool(enabled),
            raw={"transport": "hl", "property": self.property_name, "value": value},
        )


__all__ = [
    "DEFAULT_HL_MOTION_TAG_MODELS",
    "HL_MOTION_TAG_PROPERTY",
    "HlControlError",
    "HlControlUnsupported",
    "HualaiBridgePropertyTransport",
    "WyzeHlMotionTagAdapter",
    "decode_motion_tag_value",
]
