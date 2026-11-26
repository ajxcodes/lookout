"""Wyze device-management cloud adapter (IoT capability read/write).

Calls the Wyze device-management REST endpoints (``get_iot_prop`` /
``run_action``) using the reconstructed dynamic signature. No Agora/RTC
media path is involved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lookout.adapters.wyze_signing import WyzeSigner
from lookout.domain.models import MotionTagState, MotionTagWriteResult

try:  # pragma: no cover - optional dependency guard
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


_MOTION_TAG_CAPABILITY = "motion-detection"
_MOTION_TAG_PROPERTY = "motion-detection::motion-tag"

# Models known to accept the ``motion-detection::motion-tag`` cloud property.
# ``wyze_sdk.models.devices.DeviceModels.CAMERA_V4 == ["HL_CAM4"]`` (Cam v4).
# Add ascent/BulbCam-class models here, or pass an explicit ``model_allowlist``
# to ``WyzeMotionTagAdapter``.
DEFAULT_CAPABLE_MOTION_TAG_MODELS = frozenset({"HL_CAM4"})


@dataclass
class IotPropertyResult:
    device_id: str
    product_model: str
    capability: str
    properties: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    capability_iid: int | None = None


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
    ) -> None:
        self.config = config
        self.native_adapter = native_adapter
        self._user_id = user_id
        self._http_client = http_client
        if signer is not None:
            self.signer = signer
        else:
            token = getattr(config, "auth_token", None) if config is not None else None
            self.signer = WyzeSigner(access_token=token)
        # Allow signer token to track config.
        if self._user_id is None and config is not None:
            self._user_id = getattr(config, "wyze_user_id", None)

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

    def _user(self, user_id: str | None) -> str | None:
        return user_id if user_id is not None else self._user_id

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
                if self._user_id is None and cfg is not None:
                    self._user_id = getattr(cfg, "wyze_user_id", None)
                return True
            return False
        # Fallback: rebuild signer from config token.
        cfg = self.config
        token = getattr(cfg, "auth_token", None) if cfg is not None else None
        if token:
            self.signer.access_token = token
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

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self._base_url()}{path}"
        headers = self.signer.sign("POST", path, body, int(time.time() * 1000))
        client = self._http_client
        if client is not None:
            return await client.post(url, json=body, headers=headers)
        if httpx is None:
            raise RuntimeError("httpx is required for WyzeIotCloudAdapter")
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            return await c.post(url, json=body, headers=headers)

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
    def _extract_capability_iid(data: Any) -> int | None:
        """Return the first capability ``iid`` carried by a response, if any."""
        candidates: list[Any] = []
        if isinstance(data, dict):
            if isinstance(data.get("capabilities"), list):
                candidates.append(data["capabilities"])
            inner = data.get("data")
            if isinstance(inner, dict) and isinstance(inner.get("capabilities"), list):
                candidates.append(inner["capabilities"])
        for caps in candidates:
            for item in caps:
                if isinstance(item, dict) and item.get("iid") is not None:
                    try:
                        return int(item["iid"])
                    except (TypeError, ValueError):
                        return None
        return None

    async def get_iot_prop(
        self,
        device_id: str,
        product_model: str,
        *,
        user_id: str | None = None,
        capabilities: list[str] | None = None,
        capability_iids: dict[str, int] | None = None,
    ) -> IotPropertyResult:
        uid = self._user(user_id)
        caps = capabilities or [_MOTION_TAG_CAPABILITY]
        capability_list = []
        for name in caps:
            entry: dict[str, Any] = {
                "name": name,
                "properties": [{"separator": "::", "prop": f"{name}::motion-tag"}],
            }
            iid = (capability_iids or {}).get(name)
            if iid is not None:
                entry["iid"] = int(iid)
            capability_list.append(entry)
        body: dict[str, Any] = {
            "targetInfo": {
                "id": device_id,
                "productModel": product_model,
                "type": "DEVICE",
            },
            "userId": uid,
            "capabilities": capability_list,
        }
        path = "/device-management/api/device-property/get_iot_prop"

        async def _do() -> Any:
            return await self._post(path, body)

        response = await _do()
        status = self._status(response)
        data = self._response_json(response)
        if self._is_token_expired(status, data):
            refreshed = await self._refresh_auth()
            if refreshed:
                response = await _do()
                status = self._status(response)
                data = self._response_json(response)
            if self._is_token_expired(status, data):
                from lookout.adapters.wyze_native import WyzeApiError

                raise WyzeApiError("Wyze access token expired and refresh failed")
        if status >= 400:
            raise RuntimeError(f"Wyze get_iot_prop failed: HTTP {status}: {data}")

        props = self._extract_properties(data)
        cap_name = caps[0] if caps else _MOTION_TAG_CAPABILITY
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability=cap_name,
            properties=props,
            raw=data if isinstance(data, dict) else {"raw": data},
            capability_iid=self._extract_capability_iid(data),
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
        uid = self._user(user_id)
        if properties is None:
            properties = [{"separator": "::", "prop": _MOTION_TAG_PROPERTY, "value": True}]
        capability_entry: dict[str, Any] = {"name": capability, "properties": properties}
        if capability_iid is not None:
            capability_entry["iid"] = int(capability_iid)
        capability_list = [capability_entry]
        body: dict[str, Any] = {
            "targetInfo": {
                "id": device_id,
                "productModel": product_model,
                "type": "DEVICE",
            },
            "userId": uid,
            "capabilities": capability_list,
        }
        path = "/device-management/api/action/run_action"

        async def _do() -> Any:
            return await self._post(path, body)

        response = await _do()
        status = self._status(response)
        data = self._response_json(response)
        if self._is_token_expired(status, data):
            refreshed = await self._refresh_auth()
            if refreshed:
                response = await _do()
                status = self._status(response)
                data = self._response_json(response)
            if self._is_token_expired(status, data):
                from lookout.adapters.wyze_native import WyzeApiError

                raise WyzeApiError("Wyze access token expired and refresh failed")
        if status >= 400:
            raise RuntimeError(f"Wyze run_action failed: HTTP {status}: {data}")
        code = data.get("code") if isinstance(data, dict) else None
        if code is not None and str(code) not in {"1", "0", "200"}:
            msg = data.get("msg") if isinstance(data, dict) else None
            # Only treat obvious failure codes; be lenient.
            if str(code).startswith("4") or str(code).startswith("5"):
                raise RuntimeError(f"Wyze run_action failed: code={code} msg={msg}")
        return {"status": "ok", "response": data}

    async def get_motion_tag(
        self, device_id: str, product_model: str, *, user_id: str | None = None
    ) -> bool | None:
        try:
            result = await self.get_iot_prop(
                device_id, product_model, user_id=user_id, capabilities=["motion-detection"]
            )
        except Exception:  # noqa: BLE001
            return None
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
        self.cameras = list(cameras or [])
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

    def _camera(self, camera_id: str) -> Any:
        for camera in self.cameras:
            if str(getattr(camera, "id", "")) == str(camera_id):
                return camera
        return None

    def _request_user_id(self) -> str | None:
        if self._user_id is not None:
            return self._user_id
        native = self.native_adapter
        if native is not None:
            native_user_id = getattr(native, "user_id", None)
            if native_user_id:
                return str(native_user_id)
        cloud_user_id = getattr(self.iot_cloud, "_user_id", None)
        return str(cloud_user_id) if cloud_user_id else None

    @staticmethod
    def _identity_from_camera(camera: Any) -> tuple[str, str] | None:
        provider = str(getattr(camera, "native_provider", "") or "").lower()
        stream_url = str(getattr(camera, "stream_url", "") or "")
        if provider != "wyze" and not stream_url.startswith("wyze://"):
            return None
        device_id = getattr(camera, "native_device_id", None)
        model: str | None = None
        if stream_url.startswith("wyze://"):
            query = parse_qs(urlsplit(stream_url).query)
            mac_values = query.get("mac") or []
            if not device_id and mac_values:
                device_id = mac_values[0]
            model_values = query.get("model") or []
            if model_values:
                model = model_values[0]
        if not device_id or not model:
            return None
        return str(device_id), str(model)

    def _resolve_identity(self, camera_id: str) -> tuple[str, str] | None:
        cid = str(camera_id)
        cached = self._identities.get(cid)
        if cached is not None:
            return cached
        identity: tuple[str, str] | None = None
        if self.device_resolver is not None:
            try:
                resolved = self.device_resolver(cid)
            except Exception:  # noqa: BLE001 - failed resolution is retried later
                resolved = None
            if resolved:
                identity = (str(resolved[0]), str(resolved[1]))
        if identity is None and self.native_adapter is not None:
            resolver = getattr(self.native_adapter, "resolve_device_identity", None)
            if callable(resolver):
                try:
                    identity = resolver(cid)
                except Exception:  # noqa: BLE001 - failed resolution is retried later
                    identity = None
                if identity is not None:
                    identity = (str(identity[0]), str(identity[1]))
        if identity is None:
            camera = self._camera(cid)
            if camera is not None:
                identity = self._identity_from_camera(camera)
        if identity is None:
            return None
        device_id, model = identity
        if not device_id or not model:
            return None
        self._identities[cid] = (device_id, model)
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
