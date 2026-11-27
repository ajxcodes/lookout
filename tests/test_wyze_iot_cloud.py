import asyncio
import base64
import json

import pytest

from lookout.adapters.wyze_iot_cloud import (
    IotPropertyResult,
    WyzeIotCloudAdapter,
    WyzeProtocolError,
)
from lookout.adapters.wyze_native import WyzeAuthError
from lookout.adapters.wyze_signing import SignedRequest, WyzeSigner
from lookout.config.schema import WyzeConfig


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _MockClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def post(self, url, content=None, headers=None, json=None):
        self.calls.append(
            {
                "url": url,
                "content": content,
                "json": json,
                "headers": headers or {},
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return _Resp(200, {})


class _SpySigner(WyzeSigner):
    def __init__(self, access_token: str = "test-token-not-real"):
        super().__init__(access_token=access_token, phone_id="phone-test")
        self.signed: SignedRequest | None = None

    def sign_request(self, method, path, body, *, nonce=None, timestamp=None):
        self.signed = super().sign_request(method, path, body, nonce=nonce, timestamp=timestamp)
        return self.signed


def _run(coro):
    return asyncio.run(coro)


def _jwt(user_id: str) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"user_id": user_id}, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"test-header.{payload}.test-signature"


def _ok_response(*, iid=7, value=True):
    return _Resp(
        200,
        {
            "code": 1,
            "data": {
                "capabilities": [
                    {
                        "name": "motion-detection",
                        "iid": iid,
                        "properties": [{"prop": "motion-detection::motion-tag", "value": value}],
                    }
                ]
            },
        },
    )


def test_get_iot_prop_motion_tag_returns_typed_result():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_ok_response()])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    result = _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))
    assert isinstance(result, IotPropertyResult)
    assert result.properties["motion-detection::motion-tag"] is True


def test_get_iot_prop_uses_signer_headers():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_ok_response(value=False)])
    signer = WyzeSigner(access_token="tok", phone_id="phone-test")
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, signer=signer, user_id="u1")
    _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))
    assert client.calls, "no request made"
    headers = client.calls[0]["headers"]
    assert "signature2" in headers


def test_run_action_motion_tag_write_envelope():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 1})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    _run(adapter.run_action("dev1", "WYZEC1", user_id="u1"))
    body = json.loads(client.calls[0]["content"])
    assert body["capabilities"][0]["properties"][0]["separator"] == "::"
    assert body["capabilities"][0]["properties"][0]["prop"] == "motion-detection::motion-tag"


def test_retries_on_token_expiry():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")

    class _Native:
        def __init__(self):
            self.refreshed = 0

        async def _refresh_wyze_auth(self):
            self.refreshed += 1
            cfg.auth_token = "tok2"
            return True

    native = _Native()
    client = _MockClient(
        [
            _Resp(401, {"code": 2001, "msg": "access token has expired"}),
            _ok_response(),
        ]
    )
    adapter = WyzeIotCloudAdapter(
        config=cfg, http_client=client, user_id="u1", native_adapter=native
    )
    result = _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))
    assert native.refreshed == 1
    assert result.properties["motion-detection::motion-tag"] is True


def test_unsupported_model_returns_none():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 1, "data": {}})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    val = _run(adapter.get_motion_tag("dev1", "WYZEC1", user_id="u1"))
    assert val is None


def test_get_iot_prop_envelope_carries_explicit_null_iid_and_nonce():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_ok_response()])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))

    body = json.loads(client.calls[0]["content"])
    headers = client.calls[0]["headers"]
    assert "iid" in body["capabilities"][0]
    assert body["capabilities"][0]["iid"] is None
    assert "nonce" in body
    assert body["userId"] == "u1"
    assert "signature2" in headers


def test_post_signs_exact_bytes_it_sends():
    cfg = WyzeConfig(
        auth_token="test-token-not-real", device_management_base_url="https://example.test"
    )
    client = _MockClient([_ok_response()])
    signer = _SpySigner()
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, signer=signer, user_id="u1")
    _run(adapter.get_iot_prop("TESTDEVICE01", "HL_CAM4", user_id="u1"))

    captured = client.calls[0]["content"]
    assert signer.signed is not None
    assert captured == signer.signed.body
    assert json.loads(captured)["nonce"] == signer.signed.nonce
    assert captured == json.dumps(json.loads(captured), separators=(",", ":")).encode("utf-8")


def test_capability_map_resolver_is_cached_and_used_by_write():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    resolver_calls = []

    def resolver(device_id, model):
        resolver_calls.append((device_id, model))
        return {"motion-detection": 7}

    client = _MockClient([_ok_response(), _ok_response(), _Resp(200, {"code": 1})])
    adapter = WyzeIotCloudAdapter(
        config=cfg,
        http_client=client,
        user_id="u1",
        capability_map_resolver=resolver,
    )

    _run(adapter.get_iot_prop("dev1", "HL_CAM4", user_id="u1"))
    _run(adapter.get_iot_prop("dev1", "HL_CAM4", user_id="u1"))
    assert resolver_calls == [("dev1", "HL_CAM4")]
    read = json.loads(client.calls[0]["content"])
    assert read["capabilities"][0]["iid"] == 7

    _run(adapter.run_action("dev1", "HL_CAM4", user_id="u1"))
    write = json.loads(client.calls[2]["content"])
    assert write["capabilities"][0]["iid"] == 7


def test_user_id_decoded_from_jwt_when_not_configured():
    token = _jwt("user-123")
    cfg = WyzeConfig(auth_token=token, device_management_base_url="https://example.test")
    client = _MockClient([_ok_response()])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client)

    assert adapter.user_id == "user-123"
    _run(adapter.get_iot_prop("dev1", "HL_CAM4"))
    body = json.loads(client.calls[0]["content"])
    assert body["userId"] == "user-123"


def test_missing_user_id_raises_before_http_call():
    cfg = WyzeConfig(auth_token="opaque-token", device_management_base_url="https://example.test")
    client = _MockClient([_ok_response()])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client)

    with pytest.raises(WyzeAuthError) as excinfo:
        _run(adapter.get_iot_prop("dev1", "HL_CAM4"))
    assert excinfo.value.status_code == 401
    assert client.calls == []


def test_protocol_400_surfaces_typed_502_error():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")

    class _RefreshSpy:
        def __init__(self):
            self.calls = 0

        async def _refresh_wyze_auth(self):
            self.calls += 1
            return True

    spy = _RefreshSpy()
    client = _MockClient([_Resp(400, {"code": 1001, "message": "Failed to read HTTP message"})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, native_adapter=spy, user_id="u1")
    with pytest.raises(WyzeProtocolError) as excinfo:
        _run(adapter.get_iot_prop("dev1", "HL_CAM4", user_id="u1"))
    assert excinfo.value.status_code == 502
    assert spy.calls == 0
    assert len(client.calls) == 1


# ----------------------------------------------------------------------
# #85: per-capability read envelope/parser and truthful run_action
# ----------------------------------------------------------------------
def test_get_iot_prop_capability_properties_emits_per_capability_entries():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 1, "data": {}})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")

    _run(
        adapter.get_iot_prop(
            "dev1",
            "HL_CAM4",
            user_id="u1",
            capabilities=["MotionPush", "SoundPush"],
            capability_properties={
                "MotionPush": ["MotionPush::push"],
                "SoundPush": ["SoundPush::push"],
            },
        )
    )
    body = json.loads(client.calls[0]["content"])
    assert body["capabilities"][0]["properties"] == [
        {"separator": "::", "prop": "MotionPush::push"}
    ]
    assert body["capabilities"][1]["properties"] == [{"separator": "::", "prop": "SoundPush::push"}]


def test_get_iot_prop_parses_per_capability_properties_without_collision():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient(
        [
            _Resp(
                200,
                {
                    "code": 1,
                    "data": {
                        "capabilities": [
                            {
                                "name": "MotionPush",
                                "iid": 11,
                                "properties": [{"prop": "push", "value": True}],
                            },
                            {
                                "name": "SoundPush",
                                "iid": 12,
                                "properties": [{"prop": "push", "value": False}],
                            },
                        ]
                    },
                },
            )
        ]
    )
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    result = _run(
        adapter.get_iot_prop(
            "dev1",
            "HL_CAM4",
            user_id="u1",
            capabilities=["MotionPush", "SoundPush"],
            capability_properties={
                "MotionPush": ["MotionPush::push"],
                "SoundPush": ["SoundPush::push"],
            },
        )
    )

    assert result.properties_by_capability["MotionPush"]["push"] is True
    assert result.properties_by_capability["SoundPush"]["push"] is False
    assert result.properties["push"] is False


def test_run_action_rejects_business_error_code_1001():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 1001, "msg": "Invalid property"})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")

    with pytest.raises(WyzeProtocolError) as excinfo:
        _run(adapter.run_action("dev1", "HL_CAM4", user_id="u1"))
    assert excinfo.value.status_code == 502


def test_run_action_accepts_code_zero():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 0})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")

    result = _run(adapter.run_action("dev1", "HL_CAM4", user_id="u1"))
    assert result["status"] == "ok"
