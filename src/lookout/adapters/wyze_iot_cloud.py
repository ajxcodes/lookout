"""Wyze device-management cloud adapter (IoT capability read/write).

Calls the Wyze device-management REST endpoints (``get_iot_prop`` /
``run_action``) with the pinned ``signature2`` scheme. The request body is
serialized exactly once, signed, and sent as ``content`` so the digest always
covers the bytes on the wire. No Agora/RTC media path is involved.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lookout.adapters.wyze_device_identity import (
    identity_from_camera,
    iter_cameras,
    resolve_wyze_device_identity,
)
from lookout.adapters.wyze_identity import decode_jwt_user_id
from lookout.adapters.wyze_signing import SignedRequest, WyzeSigner
from lookout.domain.models import MotionTagState, MotionTagWriteResult

try:  # pragma: no cover - optional dependency guard
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # pragma: no cover - the native adapter is always importable in-tree
    from lookout.adapters.wyze_native import WyzeAuthError as _WyzeAuthError
except Exception:  # pragma: no cover - optional dependency guard

    class _WyzeAuthError(RuntimeError):  # type: ignore[no-redef]
        """Fallback auth error when the native adapter cannot be imported."""


logger = logging.getLogger(__name__)

_MOTION_TAG_CAPABILITY = "motion-detection"
_MOTION_TAG_PROPERTY = "motion-detection::motion-tag"

# Models known to accept the ``motion-detection::motion-tag`` cloud property.
# ``wyze_sdk.models.devices.DeviceModels.CAMERA_V4 == ["HL_CAM4"]`` (Cam v4).
# The allowlist is configurable through ``WyzeConfig.motion_tag_models``; this
# constant is the default and the documented fallback.
DEFAULT_CAPABLE_MOTION_TAG_MODELS = frozenset({"HL_CAM4"})


@dataclass
class IotPropertyResult:
    device_id: str
    product_model: str
    capability: str
    properties: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    capability_iid: int | None = None
    capability_iids: dict[str, int] = field(default_factory=dict)
    capability_map_known: bool = False


class WyzeProtocolError(RuntimeError):
    """Raised when the device-management API rejects a schema-valid request."""

    status_code = 502


class WyzeTransportError(WyzeProtocolError):
    """Raised when the device-management transport itself fails."""


class WyzeDeviceManagementAuthError(_WyzeAuthError):
    """Raised when the account user id cannot be resolved before a request."""

    status_code = 401


class WyzeIotCloudAdapter:
    """Cloud adapter for Wyze device-management IoT capability calls."""

    def __init__(
        self,
        config: Any = None,
        *,
        signer: WyzeSigner | None = None,
        native_adapter: Any = None,
        client: Any = None,
        user_id: str | None = None,
        http_client: Any = None,
        capability_map_resolver: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.config = config
        self.native_adapter = native_adapter
        self.capability_map_resolver = capability_map_resolver
        self._explicit_user_id = user_id
        self._user_id = user_id
        self._user_id_token: str | None = None
        self._http_client = http_client if http_client is not None else client
        self._capability_iids: dict[str, dict[str, int]] = {}
        self._capability_known: set[str] = set()
        if signer is not None:
            self.signer = signer
        else:
            token = getattr(config, "auth_token", None) if config is not None else None
            scheme = (
                getattr(config, "device_management_signature_scheme", "signature2")
                if config is not None
                else "signature2"
            )
            self.signer = WyzeSigner(access_token=token, scheme=scheme)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def _base_url(self) -> str:
        cfg = self.config
        url = getattr(cfg, "device_management_base_url", None) if cfg is not None else None
        if not url:
            url = "https://devicemgmt-service.wyze.com"
        return str(url).rstrip("/")

    def _timeout(self) -> float:
        cfg = self.config
        return float(getattr(cfg, "wcsa_timeout_seconds", 30.0) if cfg is not None else 30.0)

    def _configured_user_id(self) -> str | None:
        cfg = self.config
        if cfg is None:
            return None
        return getattr(cfg, "user_id", None) or getattr(cfg, "wyze_user_id", None)

    def _configured_capability_path(self) -> str | None:
        cfg = self.config
        if cfg is None:
            return None
        path = getattr(cfg, "device_capability_path", None)
        return str(path) if path else None

    def _user(self, user_id: str | None) -> str | None:
        """Return the per-call user id override, else the resolved account id."""
        if user_id:
            return str(user_id)
        try:
            return self._resolve_user_id()
        except WyzeDeviceManagementAuthError:
            return None

    # ------------------------------------------------------------------
    # User id resolution
    # ------------------------------------------------------------------
    def _resolve_user_id(self) -> str:
        """Resolve the account user id, caching it per access token."""
        token = self.signer.access_token
        if self._user_id and self._user_id_token == token:
            return str(self._user_id)
        candidates = (
            self._explicit_user_id,
            self._configured_user_id(),
            decode_jwt_user_id(token),
            getattr(self.native_adapter, "user_id", None),
        )
        for candidate in candidates:
            if candidate:
                self._user_id = str(candidate)
                self._user_id_token = token
                return self._user_id
        raise WyzeDeviceManagementAuthError("Wyze user id unavailable; re-authenticate")

    @property
    def user_id(self) -> str | None:
        """Return the resolved account user id, or ``None`` when unavailable."""
        try:
            return self._resolve_user_id()
        except _WyzeAuthError:
            return None

    # ------------------------------------------------------------------
    # Auth refresh
    # ------------------------------------------------------------------
    async def _refresh_auth(self) -> bool:
        native = self.native_adapter
        if native is not None and hasattr(native, "_refresh_wyze_auth"):
            try:
                refreshed = await native._refresh_wyze_auth()
            except Exception:  # noqa: BLE001
                refreshed = False
            if refreshed:
                cfg = self.config if self.config is not None else getattr(native, "config", None)
                token = getattr(cfg, "auth_token", None) if cfg is not None else None
                if token is None and native is not None:
                    token = getattr(getattr(native, "config", None), "auth_token", None)
                self.signer.access_token = token
                # The cached user id belonged to the old token; re-resolve.
                self._user_id_token = None
                return True
            return False
        # Fallback: rebuild signer from config token.
        cfg = self.config
        token = getattr(cfg, "auth_token", None) if cfg is not None else None
        if token:
            self.signer.access_token = token
            self._user_id_token = None
            return True
        return False

    def _is_token_expired(self, status: int, body: Any) -> bool:
        if status == 401:
            return True
        if isinstance(body, dict):
            code = body.get("code")
            msg = str(body.get("msg") or body.get("message") or "").lower()
            if str(code) in {"2001", "401"}:
                return True
            if "access token has expired" in msg or "token expired" in msg:
                return True
        return False

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    async def _send(self, path: str, signed: SignedRequest) -> Any:
        url = f"{self._base_url()}{path}"
        client = self._http_client
        if client is not None:
            return await client.post(url, content=signed.body, headers=signed.headers)
        if httpx is None:
            raise RuntimeError("httpx is required for WyzeIotCloudAdapter")
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            return await c.post(url, content=signed.body, headers=signed.headers)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        signed = self.signer.sign_request("POST", path, body)
        return await self._send(path, signed)

    async def _request(self, path: str, build_body: Callable[[], dict[str, Any]]) -> Any:
        """Sign, send, refresh once on token expiry, and return the parsed JSON."""
        refreshed = False
        while True:
            signed = self.signer.sign_request("POST", path, build_body())
            response = await self._send(path, signed)
            status = self._status(response)
            data = self._response_json(response)
            if self._is_token_expired(status, data):
                if not refreshed and await self._refresh_auth():
                    refreshed = True
                    continue
                raise WyzeDeviceManagementAuthError("Wyze access token expired and refresh failed")
            if status >= 400:
                raise WyzeProtocolError(f"Wyze request failed: HTTP {status}: {data}")
            return data

    @staticmethod
    def _response_json(response: Any) -> Any:
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            text = getattr(response, "text", "")
            try:
                import json

                return json.loads(text)
            except Exception:  # noqa: BLE001
                return {"raw": text}

    @staticmethod
    def _status(response: Any) -> int:
        return int(getattr(response, "status_code", getattr(response, "status", 200)))

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    def _extract_properties(self, data: Any) -> dict[str, Any]:
        props: dict[str, Any] = {}
        # Walk common shapes.
        candidates: list[Any] = []
        if isinstance(data, dict):
            if "capabilities" in data:
                candidates.append(data["capabilities"])
            if "data" in data and isinstance(data["data"], dict):
                if "capabilities" in data["data"]:
                    candidates.append(data["data"]["capabilities"])
                if "properties" in data["data"]:
                    candidates.append(data["data"]["properties"])
            if "properties" in data:
                candidates.append(data["properties"])
            if "result" in data:
                candidates.append(data["result"])
        if isinstance(data, list):
            candidates.append(data)

        for cand in candidates:
            if isinstance(cand, list):
                for item in cand:
                    if isinstance(item, dict):
                        # capability with properties
                        for p in item.get("properties", []) or []:
                            if isinstance(p, dict):
                                key = p.get("prop") or p.get("name") or p.get("key")
                                if key is not None:
                                    props[str(key)] = p.get("value")
                        # flat property entries
                        if "prop" in item and "value" in item:
                            props[str(item["prop"])] = item.get("value")
                        if "name" in item and "value" in item and "prop" not in item:
                            props[str(item["name"])] = item.get("value")
            elif isinstance(cand, dict):
                for k, v in cand.items():
                    if isinstance(v, dict) and "value" in v:
                        props[str(k)] = v.get("value")
                    else:
                        props[str(k)] = v
        return props

    @staticmethod
    def _coerce_iid(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        if isinstance(value, dict):
            return WyzeIotCloudAdapter._coerce_iid(value.get("iid"))
        return None

    @classmethod
    def _extract_capability_iids(cls, data: Any) -> dict[str, int]:
        """Return ``{capability_name: iid}`` from a response, if present."""
        result: dict[str, int] = {}
        if not isinstance(data, dict):
            return result
        containers: list[dict[str, Any]] = [data]
        inner = data.get("data")
        if isinstance(inner, dict):
            containers.append(inner)
        for container in containers:
            raw_maps = container.get("iotIidMaps")
            if isinstance(raw_maps, dict):
                for name, value in raw_maps.items():
                    iid = cls._coerce_iid(value)
                    if iid is not None:
                        result[str(name)] = iid
            elif isinstance(raw_maps, list):
                for item in raw_maps:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("capability")
                        iid = cls._coerce_iid(item.get("iid"))
                        if name and iid is not None:
                            result[str(name)] = iid
            caps = container.get("capabilities")
            if isinstance(caps, list):
                for item in caps:
                    if isinstance(item, dict):
                        name = item.get("name")
                        iid = cls._coerce_iid(item.get("iid"))
                        if name and iid is not None:
                            result[str(name)] = iid
        return result

    @classmethod
    def _extract_capability_iid(cls, data: Any) -> int | None:
        """Compatibility wrapper: the ``motion-detection`` iid when present."""
        iids = cls._extract_capability_iids(data)
        if _MOTION_TAG_CAPABILITY in iids:
            return iids[_MOTION_TAG_CAPABILITY]
        return next(iter(iids.values()), None)

    # ------------------------------------------------------------------
    # Capability map (``iotIidMaps``)
    # ------------------------------------------------------------------
    def clear_capability_cache(self) -> None:
        """Drop every cached per-device capability map."""
        self._capability_iids.clear()
        self._capability_known.clear()

    def _cache_capability_map(self, device_id: str, iids: dict[str, int]) -> None:
        self._capability_iids[str(device_id)] = dict(iids)
        self._capability_known.add(str(device_id))

    async def _load_capability_map(
        self, device_id: str, model: str, user_id: str
    ) -> dict[str, int] | None:
        """Resolve the capability map, or ``None`` when it is unknown.

        Failures are logged by field presence only and are never cached, so a
        later call can retry instead of pinning the device to ``unsupported``.
        """
        device_id = str(device_id)
        if device_id in self._capability_known:
            return dict(self._capability_iids.get(device_id, {}))

        resolver = self.capability_map_resolver
        if resolver is not None:
            try:
                resolved = resolver(device_id, model)
                if inspect.isawaitable(resolved):
                    resolved = await resolved
            except Exception:  # noqa: BLE001 - unknown map degrades to explicit null
                logger.debug("Wyze capability-map resolver failed; iid unknown")
                return None
            if isinstance(resolved, dict):
                iids = {
                    str(name): iid
                    for name, iid in (
                        (name, self._coerce_iid(value)) for name, value in resolved.items()
                    )
                    if iid is not None
                }
                self._cache_capability_map(device_id, iids)
                return dict(iids)
            return None

        path = self._configured_capability_path()
        if not path:
            return None
        body = {
            "targetInfo": {"id": device_id, "productModel": model, "type": "DEVICE"},
            "userId": user_id,
        }
        try:
            response = await self._post(path, body)
            status = self._status(response)
            data = self._response_json(response)
            if status >= 400 or self._is_token_expired(status, data):
                logger.debug("Wyze capability-map endpoint rejected the request")
                return None
            iids = self._extract_capability_iids(data)
        except Exception:  # noqa: BLE001 - unknown map degrades to explicit null
            logger.debug("Wyze capability-map lookup failed; iid unknown")
            return None
        self._cache_capability_map(device_id, iids)
        return dict(iids)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def get_iot_prop(
        self,
        device_id: str,
        product_model: str,
        *,
        user_id: str | None = None,
        capabilities: list[str] | None = None,
        properties: list[str] | None = None,
        capability_iids: dict[str, int] | None = None,
    ) -> IotPropertyResult:
        caps = list(capabilities or [_MOTION_TAG_CAPABILITY])

        uid = self._resolve_user_id() if not user_id else str(user_id)
        if capability_iids is not None:
            iid_map = {
                str(name): iid
                for name, iid in (
                    (name, self._coerce_iid(value)) for name, value in capability_iids.items()
                )
                if iid is not None
            }
            self._cache_capability_map(device_id, iid_map)
            map_known = True
        else:
            loaded = await self._load_capability_map(device_id, product_model, uid)
            iid_map = dict(loaded or {})
            map_known = loaded is not None

        def build_body() -> dict[str, Any]:
            entries: list[dict[str, Any]] = []
            for name in caps:
                iid = iid_map.get(name)
                names = list(properties) if properties else [f"{name}::motion-tag"]
                entries.append(
                    {
                        "name": name,
                        "iid": int(iid) if iid is not None else None,
                        "properties": [{"separator": "::", "prop": prop} for prop in names],
                    }
                )
            return {
                "targetInfo": {
                    "id": device_id,
                    "productModel": product_model,
                    "type": "DEVICE",
                },
                "userId": self._resolve_user_id() if not user_id else str(user_id),
                "capabilities": entries,
            }

        path = "/device-management/api/device-property/get_iot_prop"
        data = await self._request(path, build_body)

        props = self._extract_properties(data)
        response_iids = self._extract_capability_iids(data)
        if response_iids:
            iid_map = {**iid_map, **response_iids}
            self._cache_capability_map(device_id, iid_map)
            map_known = True
        cap_name = caps[0] if caps else _MOTION_TAG_CAPABILITY
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability=cap_name,
            properties=props,
            raw=data if isinstance(data, dict) else {"raw": data},
            capability_iid=iid_map.get(cap_name),
            capability_iids=dict(iid_map),
            capability_map_known=map_known,
        )

    async def run_action(
        self,
        device_id: str,
        product_model: str,
        *,
        user_id: str | None = None,
        capability: str = _MOTION_TAG_CAPABILITY,
        properties: list[dict[str, Any]] | None = None,
        capability_iid: int | None = None,
    ) -> dict[str, Any]:
        if properties is None:
            properties = [{"separator": "::", "prop": _MOTION_TAG_PROPERTY, "value": True}]
        iid = (
            int(capability_iid)
            if capability_iid is not None
            else self._capability_iids.get(str(device_id), {}).get(capability)
        )

        def build_body() -> dict[str, Any]:
            return {
                "targetInfo": {
                    "id": device_id,
                    "productModel": product_model,
                    "type": "DEVICE",
                },
                "userId": self._resolve_user_id() if not user_id else str(user_id),
                "capabilities": [
                    {
                        "name": capability,
                        "iid": int(iid) if iid is not None else None,
                        "properties": properties,
                    }
                ],
            }

        path = "/device-management/api/action/run_action"
        data = await self._request(path, build_body)
        code = data.get("code") if isinstance(data, dict) else None
        if code is not None and str(code) not in {"1", "0", "200"}:
            msg = data.get("msg") if isinstance(data, dict) else None
            # Only treat obvious failure codes; be lenient.
            if str(code).startswith("4") or str(code).startswith("5"):
                raise WyzeProtocolError(f"Wyze run_action failed: code={code} msg={msg}")
        return {"status": "ok", "response": data}

    async def get_motion_tag(
        self, device_id: str, product_model: str, *, user_id: str | None = None
    ) -> bool | None:
        result = await self.get_iot_prop(
            device_id, product_model, user_id=user_id, capabilities=["motion-detection"]
        )
        for key, val in (result.properties or {}).items():
            if "motion-tag" in str(key):
                if isinstance(val, bool):
                    return val
                if val is None:
                    return None
                return bool(val)
        return None

    async def set_motion_tag(
        self,
        device_id: str,
        product_model: str,
        *,
        enabled: bool,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.run_action(
            device_id,
            product_model,
            user_id=user_id,
            capability="motion-detection",
            properties=[
                {"separator": "::", "prop": "motion-detection::motion-tag", "value": bool(enabled)}
            ],
            capability_iid=self._capability_iids.get(str(device_id), {}).get(
                _MOTION_TAG_CAPABILITY
            ),
        )


class WyzeMotionTagAdapter:
    """Camera-aware ``MotionTagPort`` over the device-management cloud adapter.

    Resolves ``camera_id`` to a ``(device_id, product_model)`` pair, caches the
    per-device ``motion-detection`` capability probe (including its ``iid``),
    and degrades explicitly to an unsupported state for devices that do not
    advertise the capability (for example cam v3's HL stack).
    """

    _UNSUPPORTED_MARKERS = (
        "unsupported",
        "not support",
        "not supported",
        "no such capability",
        "capability not found",
        "invalid capability",
    )

    def __init__(
        self,
        *,
        iot_cloud: WyzeIotCloudAdapter,
        cameras: Any = (),
        device_resolver: Any = None,
        native_adapter: Any = None,
        model_allowlist: Any = None,
        user_id: str | None = None,
    ) -> None:
        self.iot_cloud = iot_cloud
        self._cameras_source = cameras
        self.cameras = cameras if callable(cameras) else list(cameras or [])
        self.device_resolver = device_resolver
        self.native_adapter = native_adapter
        self.model_allowlist: set[str] = (
            set(model_allowlist)
            if model_allowlist is not None
            else set(DEFAULT_CAPABLE_MOTION_TAG_MODELS)
        )
        self._user_id = user_id
        self._identities: dict[str, tuple[str, str]] = {}
        self._states: dict[str, MotionTagState] = {}
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

    def _model_supported(self, model: str) -> bool:
        return str(model) in self.model_allowlist

    @classmethod
    def _is_unsupported_error(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in cls._UNSUPPORTED_MARKERS)

    # ------------------------------------------------------------------
    # MotionTagPort
    # ------------------------------------------------------------------
    async def get_motion_tag(self, camera_id: str) -> MotionTagState:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return MotionTagState(supported=False, enabled=None)
        device_id, model = identity
        if not self._model_supported(model):
            return MotionTagState(supported=False, enabled=None)
        cached = self._states.get(device_id)
        if cached is not None:
            return cached
        return await self._probe(device_id, model)

    async def _probe(self, device_id: str, model: str) -> MotionTagState:
        cached_iid = self._capability_iids.get(device_id)
        iids = {_MOTION_TAG_CAPABILITY: cached_iid} if cached_iid is not None else None
        try:
            result = await self.iot_cloud.get_iot_prop(
                device_id,
                model,
                user_id=self._request_user_id(),
                capabilities=[_MOTION_TAG_CAPABILITY],
                capability_iids=iids,
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                state = MotionTagState(supported=False, enabled=None)
                self._states[device_id] = state
                return state
            raise

        iid = getattr(result, "capability_iid", None)
        if isinstance(iid, int) and not isinstance(iid, bool):
            self._capability_iids[device_id] = iid

        if getattr(result, "capability_map_known", False):
            known = getattr(result, "capability_iids", None) or {}
            if _MOTION_TAG_CAPABILITY not in known:
                state = MotionTagState(supported=False, enabled=None)
                self._states[device_id] = state
                return state

        value: bool | None = None
        found = False
        for key, raw_value in (getattr(result, "properties", None) or {}).items():
            if "motion-tag" in str(key):
                found = True
                if raw_value is not None:
                    value = bool(raw_value)
                break
        if not found or value is None:
            state = MotionTagState(supported=False, enabled=None)
        else:
            state = MotionTagState(supported=True, enabled=value)
        self._states[device_id] = state
        return state

    async def set_motion_tag(self, camera_id: str, enabled: bool) -> MotionTagWriteResult:
        identity = self._resolve_identity(camera_id)
        if identity is None:
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        device_id, model = identity
        if not self._model_supported(model):
            return MotionTagWriteResult(
                supported=False, enabled=None, raw={"reason": "unsupported_model"}
            )
        state = self._states.get(device_id)
        if state is None:
            state = await self._probe(device_id, model)
        if not state.supported:
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        wanted = bool(enabled)
        try:
            raw = await self.iot_cloud.run_action(
                device_id,
                model,
                user_id=self._request_user_id(),
                capability=_MOTION_TAG_CAPABILITY,
                properties=[
                    {
                        "separator": "::",
                        "prop": _MOTION_TAG_PROPERTY,
                        "value": wanted,
                    }
                ],
                capability_iid=self._capability_iids.get(device_id),
            )
        except Exception as exc:  # noqa: BLE001 - unsupported degrades, transport re-raises
            if self._is_unsupported_error(exc):
                self._states[device_id] = MotionTagState(supported=False, enabled=None)
                return MotionTagWriteResult(supported=False, enabled=None, raw={})
            raise
        self._states[device_id] = MotionTagState(supported=True, enabled=wanted)
        return MotionTagWriteResult(
            supported=True,
            enabled=wanted,
            raw=raw if isinstance(raw, dict) else {},
        )
