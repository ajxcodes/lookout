import hashlib
import json

import pytest

from lookout.adapters.wyze_signing import (
    WYZE_ANDROID_APP_VERSION,
    WYZE_APP_ID,
    WYZE_APP_SECRET_SALT,
    SignedRequest,
    WyzeSigner,
    WyzeSigningError,
)

_GOLDEN_BODY = (
    b'{"targetInfo":{"id":"TESTDEVICE01","productModel":"HL_CAM4","type":"DEVICE"},'
    b'"userId":"test-user-id","capabilities":[{"name":"motion-detection","iid":7,'
    b'"properties":[{"separator":"::","prop":"motion-detection::motion-tag","value":true}]}],'
    b'"nonce":"1700000000000"}'
)


def _golden_payload() -> dict:
    return {
        "targetInfo": {"id": "TESTDEVICE01", "productModel": "HL_CAM4", "type": "DEVICE"},
        "userId": "test-user-id",
        "capabilities": [
            {
                "name": "motion-detection",
                "iid": 7,
                "properties": [
                    {"separator": "::", "prop": "motion-detection::motion-tag", "value": True}
                ],
            }
        ],
    }


def test_signature2_golden_vector_matches_wyze_sdk_scheme():
    signer = WyzeSigner(access_token="test-token-not-real", phone_id="phone-test")
    signed = signer.sign_request(
        "POST",
        "/device-management/api/device-property/get_iot_prop",
        _golden_payload(),
        nonce="1700000000000",
    )

    assert isinstance(signed, SignedRequest)
    assert signed.body == _GOLDEN_BODY
    assert signed.headers["signature2"] == "6036444cd0618fd3ca05a146546e7361"
    assert signed.headers["requestid"] == "474ecf29d67d8a9adc6d1e24393fc616"


def test_signature2_derived_key_matches_pinned_sdk():
    signer = WyzeSigner(access_token="test-token-not-real")
    derived = hashlib.md5(f"test-token-not-real{WYZE_APP_SECRET_SALT}".encode()).hexdigest()
    assert derived == "3a07de71bebc5a7397156ce551514b85"
    assert signer.app_id == WYZE_APP_ID == "9319141212m2ik"


def test_signature2_header_set_includes_exservice_headers():
    signer = WyzeSigner(access_token="test-token-not-real", phone_id="phone-test")
    signed = signer.sign_request("POST", "/p", {"a": 1}, nonce="1700000000000")

    assert set(signed.headers) == {
        "Authorization",
        "access_token",
        "signature2",
        "requestid",
        "appid",
        "appinfo",
        "phoneid",
        "User-Agent",
        "Content-Type",
    }
    assert signed.headers["appid"] == "9319141212m2ik"
    assert signed.headers["appinfo"] == f"wyze_android_{WYZE_ANDROID_APP_VERSION}"
    assert signed.headers["appinfo"] == "wyze_android_2.19.14"
    assert signed.headers["User-Agent"] == "wyze_android_2.19.14"
    assert signed.headers["phoneid"] == "phone-test"
    assert signed.headers["access_token"] == "test-token-not-real"


def test_signature2_injects_nonce_last_and_signs_compact_bytes():
    signer = WyzeSigner(access_token="tok", phone_id="phone-test")
    signed = signer.sign_request("POST", "/p", {"a": 1, "b": 2}, nonce="42")

    assert signed.body == b'{"a":1,"b":2,"nonce":"42"}'
    assert signed.nonce == "42"
    assert json.loads(signed.body)["nonce"] == "42"


def test_sign_request_uses_provided_timestamp_as_nonce():
    signer = WyzeSigner(access_token="tok", phone_id="phone-test")
    timestamp = 42
    signed = signer.sign_request("POST", "/p", {"a": 1}, timestamp=timestamp)
    assert signed.nonce == str(timestamp)
    expected_request_id = hashlib.md5(
        hashlib.md5(str(timestamp).encode("utf-8")).hexdigest().encode("utf-8")
    ).hexdigest()
    assert signed.headers["requestid"] == expected_request_id


def test_sign_is_deterministic():
    signer = WyzeSigner(access_token="tok", phone_id="phone-test")
    body = {"a": 1, "b": 2}
    first = signer.sign_request("POST", "/p", body, nonce="123")
    second = signer.sign_request("POST", "/p", body, nonce="123")
    assert first.headers == second.headers
    assert first.body == second.body


def test_sign_raises_wyze_signing_error_on_missing_token():
    signer = WyzeSigner(access_token=None)
    with pytest.raises(WyzeSigningError):
        signer.sign_request("POST", "/p", {}, nonce="1")


def test_legacy_signature_scheme_remains_selectable():
    legacy = WyzeSigner(access_token="test-token-not-real", scheme="legacy")
    legacy_headers = legacy.sign_request("POST", "/p", {}, nonce="1").headers
    assert "signature" in legacy_headers
    assert "timestamp" in legacy_headers

    default_headers = (
        WyzeSigner(access_token="test-token-not-real", phone_id="phone-test")
        .sign_request("POST", "/p", {}, nonce="1")
        .headers
    )
    assert "signature2" in default_headers
    assert "signature" not in default_headers


def test_golden_vector_no_network():
    # Import-only / no network access smoke.
    signer = WyzeSigner(access_token="tok")
    assert signer is not None
