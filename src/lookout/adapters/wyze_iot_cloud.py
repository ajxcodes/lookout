"""Wyze device-management cloud adapter (IoT capability read/write).

Calls the Wyze device-management REST endpoints (``get_iot_prop`` /
``run_action``) using the reconstructed dynamic signature. No Agora/RTC
media path is involved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from lookout.adapters.wyze_signing import WyzeSigner

try:  # pragma: no cover - optional dependency guard
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


@dataclass
class IotPropertyResult:
    device_id: str
    product_model: str
    capability: str
    properties: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


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
            if str(code) in {"2001", "401", "1001"}:
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

    async def get_iot_prop(
        self,
        device_id: str,
        product_model: str,
        *,
        user_id: str | None = None,
        capabilities: list[str] | None = None,
    ) -> IotPropertyResult:
        uid = self._user(user_id)
        caps = capabilities or ["motion-detection"]
        capability_list = []
        for name in caps:
            capability_list.append(
                {
                    "name": name,
                    "properties": [{"separator": "::", "prop": f"{name}::motion-tag"}],
                }
            )
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
        cap_name = caps[0] if caps else "motion-detection"
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability=cap_name,
            properties=props,
            raw=data if isinstance(data, dict) else {"raw": data},
        )

    async def run_action(
        self,
        device_id: str,
        product_model: str,
        *,
        user_id: str | None = None,
        capability: str = "motion-detection",
        properties: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        uid = self._user(user_id)
        if properties is None:
            properties = [{"separator": "::", "prop": f"{capability}::motion-tag", "value": True}]
        capability_list = [{"name": capability, "properties": properties}]
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
