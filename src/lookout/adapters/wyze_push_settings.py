"""Wyze push-notification settings sync over the device-management cloud lane (#85).

Exposes the per-device **cloud** push toggles (``MotionPush``, ``SoundPush``,
``SmartDetectionPush``, ``LowBatteryPush``, ``AiPush``, ``PushSwitch``) through
the same cloud lane as motion tagging and Pan Scan (``get_iot_prop`` read,
``run_action`` write). Lookout stays pull-based: it cannot receive FCM pushes,
so this is settings sync only, never push delivery.

``PUSH_SETTING_PROPERTIES`` is the single change point for the property leaf
names. The table (``<capability>::push``) is derived from the APK capability
names and is **unverified against a live device** until
``docs/reverse-engineering/wyze-protocol-notes.md`` §14b records an operator
confirmation. Writing therefore also requires the key to have been observed by
a successful read first, so Lookout never writes a property it has not read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lookout.adapters.wyze_device_identity import (
    identity_from_camera,
    iter_cameras,
    resolve_wyze_device_identity,
)
from lookout.domain.models import (
    PUSH_SETTING_KEYS,
    PushSettingsState,
    PushSettingsWriteResult,
)

if TYPE_CHECKING:
    from lookout.adapters.wyze_iot_cloud import WyzeIotCloudAdapter


PUSH_SETTING_PROPERTIES: dict[str, str] = {cap: f"{cap}::push" for cap in PUSH_SETTING_KEYS}

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
    can never leak into a toggle state.
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


def _table_leaf(capability: str) -> str:
    """Return the table property leaf for a capability."""
    return PUSH_SETTING_PROPERTIES[capability].rsplit("::", 1)[-1]


class WyzePushSettingsAdapter:
    """Camera-aware ``WyzePushSettingsPort`` over the device-management lane."""

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
        self._states: dict[str, PushSettingsState] = {}
        self._capability_iids: dict[str, dict[str, int]] = {}
        self._observed_leaves: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """Drop cached identities, probes, iid maps, and observed leaves."""
        self._identities.clear()
        self._states.clear()
        self._capability_iids.clear()
        self._observed_leaves.clear()

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

    @staticmethod
    def _all_unsupported() -> PushSettingsState:
        return PushSettingsState(
            supported=False,
            settings={},
            unsupported=list(PUSH_SETTING_KEYS),
        )

    # ------------------------------------------------------------------
    # Support resolution
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_value(cls, capability: str, values: dict[str, Any]) -> tuple[bool, str] | None:
        """Return ``(value, observed_leaf)`` for a capability, or ``None``.

        The table leaf wins when it carries a coercible value. Otherwise the
        capability must advertise exactly one boolean-coercible property; a
        response with several competing booleans and no table-leaf match is
        reported unsupported rather than guessed.
        """
        table_leaf = _table_leaf(capability)
        if table_leaf in values:
            coerced = _coerce_bool(values[table_leaf])
            if coerced is not None:
                return coerced, table_leaf
        candidates: list[tuple[str, bool]] = []
        for leaf, raw_value in values.items():
            coerced = _coerce_bool(raw_value)
            if coerced is not None:
                candidates.append((str(leaf), coerced))
        if len(candidates) == 1:
            leaf, coerced = candidates[0]
            return coerced, leaf
        return None

    # ------------------------------------------------------------------
    # WyzePushSettingsPort
    # ------------------------------------------------------------------
    async def get_push_settings(self, camera_id: str) -> PushSettingsState:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return self._all_unsupported()
        device_id, model = identity
        cached = self._states.get(device_id)
        if cached is not None:
            return cached
        return await self._probe(device_id, model)

    async def _probe(self, device_id: str, model: str) -> PushSettingsState:
        cached_iids = self._capability_iids.get(device_id) or None
        try:
            result = await self.iot_cloud.get_iot_prop(
                device_id,
                model,
                user_id=self._request_user_id(),
                capabilities=list(PUSH_SETTING_KEYS),
                capability_properties={
                    capability: [PUSH_SETTING_PROPERTIES[capability]]
                    for capability in PUSH_SETTING_KEYS
                },
                capability_iids=dict(cached_iids) if cached_iids else None,
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                state = self._all_unsupported()
                self._states[device_id] = state
                return state
            raise

        # Keep the full map: replacing it with a single-capability probe would
        # pin sibling capabilities as known-absent.
        iid_map = dict(getattr(result, "capability_iids", None) or {})
        single_iid = getattr(result, "capability_iid", None)
        single_capability = getattr(result, "capability", None)
        if single_capability and isinstance(single_iid, int) and not isinstance(single_iid, bool):
            iid_map.setdefault(str(single_capability), single_iid)
        if iid_map:
            self._capability_iids[device_id] = iid_map

        known_map = getattr(result, "capability_map_known", False)
        known = getattr(result, "capability_iids", None) or {}
        by_capability = getattr(result, "properties_by_capability", None) or {}

        settings: dict[str, bool] = {}
        unsupported: list[str] = []
        observed: dict[str, str] = {}
        for capability in PUSH_SETTING_KEYS:
            if known_map and capability not in known:
                unsupported.append(capability)
                continue
            values = by_capability.get(capability) or {}
            resolved = self._resolve_value(capability, values)
            if resolved is None:
                unsupported.append(capability)
                continue
            value, leaf = resolved
            settings[capability] = value
            observed[capability] = leaf

        if observed:
            self._observed_leaves[device_id] = observed
        state = PushSettingsState(
            supported=bool(settings),
            settings=settings,
            unsupported=unsupported,
        )
        self._states[device_id] = state
        return state

    async def set_push_setting(
        self, camera_id: str, key: str, enabled: bool
    ) -> PushSettingsWriteResult:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return PushSettingsWriteResult(supported=False, key=key, enabled=None, raw={})
        device_id, model = identity
        state = self._states.get(device_id)
        if state is None:
            state = await self._probe(device_id, model)
        if key not in state.settings:
            return PushSettingsWriteResult(supported=False, key=key, enabled=None, raw={})

        wanted = bool(enabled)
        leaf = self._observed_leaves.get(device_id, {}).get(key) or _table_leaf(key)
        prop = f"{key}::{leaf}"
        iid = (self._capability_iids.get(device_id) or {}).get(key)
        try:
            raw = await self.iot_cloud.run_action(
                device_id,
                model,
                user_id=self._request_user_id(),
                capability=key,
                properties=[
                    {
                        "separator": "::",
                        "prop": prop,
                        "value": wanted,
                    }
                ],
                capability_iid=iid,
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                settings = dict(state.settings)
                settings.pop(key, None)
                unsupported = list(state.unsupported)
                if key not in unsupported:
                    unsupported.append(key)
                self._states[device_id] = PushSettingsState(
                    supported=bool(settings),
                    settings=settings,
                    unsupported=unsupported,
                )
                return PushSettingsWriteResult(supported=False, key=key, enabled=None, raw={})
            raise

        settings = dict(state.settings)
        settings[key] = wanted
        self._states[device_id] = PushSettingsState(
            supported=True,
            settings=settings,
            unsupported=list(state.unsupported),
        )
        return PushSettingsWriteResult(
            supported=True,
            key=key,
            enabled=wanted,
            raw=raw if isinstance(raw, dict) else {},
        )


__all__ = ["PUSH_SETTING_PROPERTIES", "WyzePushSettingsAdapter"]
