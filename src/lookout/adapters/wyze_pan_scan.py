"""Wyze Pan Scan adapter over the device-management cloud lane (#70).

Pan Scan (continuous auto-pan across the configured span) is exposed as a
tri-state per-camera toggle over the same cloud lane used by motion tagging
(``get_iot_prop`` read, ``run_action`` write). The adapter resolves
``camera_id`` to a ``(device_id, product_model)`` pair, probes the
``camera-pnt`` capability once per device, caches the capability ``iid``, and
degrades explicitly to an unsupported state for fixed (non-pan) cameras. No
Agora/RTC media path is involved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lookout.adapters.wyze_device_identity import (
    identity_from_camera,
    iter_cameras,
    resolve_wyze_device_identity,
)
from lookout.domain.models import PanScanState, PanScanWriteResult

if TYPE_CHECKING:
    from lookout.adapters.wyze_iot_cloud import WyzeIotCloudAdapter


_PAN_SCAN_CAPABILITY = "camera-pnt"
_PAN_SCAN_PROPERTY = "camera-pnt::scan"
_PAN_TRACKING_PROPERTY = "camera-pnt::tracking"

_TRUE_STRINGS = frozenset({"1", "true", "on", "yes", "enabled"})
_FALSE_STRINGS = frozenset({"0", "false", "off", "no", "disabled"})

_UNSUPPORTED_MARKERS = (
    "unsupported",
    "not support",
    "not supported",
    "no such capability",
    "capability not found",
    "invalid capability",
)


def _coerce_bool(value: Any) -> bool | None:
    """Coerce an IoT property value to a strict bool, or ``None``.

    Strings are matched against an explicit allowlist so ``bool("0") is True``
    can never leak into the toggle state.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
    return None


def _property_leaf(name: Any) -> str:
    """Return the last ``::`` segment of an IoT property name."""
    return str(name).rsplit("::", 1)[-1]


class WyzePanScanAdapter:
    """Camera-aware ``PanScanPort`` over the device-management cloud adapter."""

    _UNSUPPORTED_MARKERS = _UNSUPPORTED_MARKERS

    def __init__(
        self,
        *,
        iot_cloud: WyzeIotCloudAdapter,
        cameras: Any = (),
        device_resolver: Any = None,
        native_adapter: Any = None,
        user_id: str | None = None,
    ) -> None:
        self.iot_cloud = iot_cloud
        self._cameras_source = cameras
        self.cameras = cameras if callable(cameras) else list(cameras or [])
        self.device_resolver = device_resolver
        self.native_adapter = native_adapter
        self._user_id = user_id
        self._identities: dict[str, tuple[str, str]] = {}
        self._states: dict[str, PanScanState] = {}
        self._capability_iids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """Drop cached identities, capability probes, and known iids."""
        self._identities.clear()
        self._states.clear()
        self._capability_iids.clear()

    def _iter_cameras(self) -> list[Any]:
        """Return cameras from the configured sequence or zero-arg callable."""
        return iter_cameras(self._cameras_source)

    def _camera(self, camera_id: str) -> Any:
        for camera in self._iter_cameras():
            if str(getattr(camera, "id", "")) == str(camera_id):
                return camera
        return None

    def _request_user_id(self) -> str | None:
        cloud = self.iot_cloud
        cloud_user_id = getattr(cloud, "user_id", None)
        if cloud_user_id:
            return str(cloud_user_id)
        native = self.native_adapter
        if native is not None:
            native_user_id = getattr(native, "user_id", None)
            if native_user_id:
                return str(native_user_id)
        if self._user_id is not None:
            return self._user_id
        return None

    @staticmethod
    def _identity_from_camera(camera: Any) -> tuple[str, str] | None:
        identity = identity_from_camera(camera)
        if identity is None:
            return None
        return identity.device_id, identity.model

    def _resolve_identity(self, camera_id: str) -> tuple[str, str] | None:
        cid = str(camera_id)
        cached = self._identities.get(cid)
        if cached is not None:
            return cached
        identity = resolve_wyze_device_identity(
            cid,
            cameras=self._cameras_source,
            device_resolver=self.device_resolver,
            native_adapter=self.native_adapter,
        )
        if identity is None:
            return None
        self._identities[cid] = (identity.device_id, identity.model)
        return self._identities[cid]

    @classmethod
    def _is_unsupported_error(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in cls._UNSUPPORTED_MARKERS)

    # ------------------------------------------------------------------
    # PanScanPort
    # ------------------------------------------------------------------
    async def get_pan_scan(self, camera_id: str) -> PanScanState:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return PanScanState(supported=False, enabled=None, tracking=None)
        device_id, model = identity
        cached = self._states.get(device_id)
        if cached is not None:
            return cached
        return await self._probe(device_id, model)

    async def _probe(self, device_id: str, model: str) -> PanScanState:
        cached_iid = self._capability_iids.get(device_id)
        iids = {_PAN_SCAN_CAPABILITY: cached_iid} if cached_iid is not None else None
        try:
            result = await self.iot_cloud.get_iot_prop(
                device_id,
                model,
                user_id=self._request_user_id(),
                capabilities=[_PAN_SCAN_CAPABILITY],
                properties=[_PAN_SCAN_PROPERTY, _PAN_TRACKING_PROPERTY],
                capability_iids=iids,
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                state = PanScanState(supported=False, enabled=None, tracking=None)
                self._states[device_id] = state
                return state
            raise

        iid = getattr(result, "capability_iid", None)
        if isinstance(iid, int) and not isinstance(iid, bool):
            self._capability_iids[device_id] = iid

        if getattr(result, "capability_map_known", False):
            known = getattr(result, "capability_iids", None) or {}
            if _PAN_SCAN_CAPABILITY not in known:
                state = PanScanState(supported=False, enabled=None, tracking=None)
                self._states[device_id] = state
                return state

        scan: bool | None = None
        tracking: bool | None = None
        found = False
        for key, raw_value in (getattr(result, "properties", None) or {}).items():
            leaf = _property_leaf(key)
            if leaf == "scan":
                found = True
                scan = _coerce_bool(raw_value)
            elif leaf == "tracking":
                tracking = _coerce_bool(raw_value)

        if not found or scan is None:
            state = PanScanState(supported=False, enabled=None, tracking=None)
        else:
            state = PanScanState(supported=True, enabled=scan, tracking=tracking)
        self._states[device_id] = state
        return state

    async def set_pan_scan(self, camera_id: str, enabled: bool) -> PanScanWriteResult:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return PanScanWriteResult(supported=False, enabled=None, raw={})
        device_id, model = identity
        state = self._states.get(device_id)
        if state is None:
            state = await self._probe(device_id, model)
        if not state.supported:
            return PanScanWriteResult(supported=False, enabled=None, raw={})
        wanted = bool(enabled)
        try:
            raw = await self.iot_cloud.run_action(
                device_id,
                model,
                user_id=self._request_user_id(),
                capability=_PAN_SCAN_CAPABILITY,
                properties=[
                    {
                        "separator": "::",
                        "prop": _PAN_SCAN_PROPERTY,
                        "value": wanted,
                    }
                ],
                capability_iid=self._capability_iids.get(device_id),
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                self._states[device_id] = PanScanState(supported=False, enabled=None, tracking=None)
                return PanScanWriteResult(supported=False, enabled=None, raw={})
            raise
        self._states[device_id] = PanScanState(
            supported=True, enabled=wanted, tracking=state.tracking
        )
        return PanScanWriteResult(
            supported=True,
            enabled=wanted,
            raw=raw if isinstance(raw, dict) else {},
        )


__all__ = ["WyzePanScanAdapter"]
