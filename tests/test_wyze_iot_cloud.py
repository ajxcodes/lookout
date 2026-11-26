import asyncio

from lookout.adapters.wyze_iot_cloud import IotPropertyResult, WyzeIotCloudAdapter
from lookout.adapters.wyze_signing import WyzeSigner
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

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if self._responses:
            return self._responses.pop(0)
        return _Resp(200, {})


def _run(coro):
    return asyncio.run(coro)


def test_get_iot_prop_motion_tag_returns_typed_result():
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
                                "name": "motion-detection",
                                "properties": [
                                    {"prop": "motion-detection::motion-tag", "value": True}
                                ],
                            }
                        ]
                    },
                },
            )
        ]
    )
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    result = _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))
    assert isinstance(result, IotPropertyResult)
    assert result.properties["motion-detection::motion-tag"] is True


def test_get_iot_prop_uses_signer_headers():
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
                                "name": "motion-detection",
                                "properties": [
                                    {"prop": "motion-detection::motion-tag", "value": False}
                                ],
                            }
                        ]
                    },
                },
            )
        ]
    )
    signer = WyzeSigner(access_token="tok")
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, signer=signer, user_id="u1")
    _run(adapter.get_iot_prop("dev1", "WYZEC1", user_id="u1"))
    assert client.calls, "no request made"
    headers = client.calls[0]["headers"]
    assert "signature" in headers


def test_run_action_motion_tag_write_envelope():
    cfg = WyzeConfig(auth_token="tok", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(200, {"code": 1})])
    adapter = WyzeIotCloudAdapter(config=cfg, http_client=client, user_id="u1")
    _run(adapter.run_action("dev1", "WYZEC1", user_id="u1"))
    body = client.calls[0]["json"]
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
            _Resp(
                200,
                {
                    "code": 1,
                    "data": {
                        "capabilities": [
                            {
                                "name": "motion-detection",
                                "properties": [
                                    {"prop": "motion-detection::motion-tag", "value": True}
                                ],
                            }
                        ]
                    },
                },
            ),
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
