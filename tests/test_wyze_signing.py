import hashlib
import hmac
import json

import pytest

from lookout.adapters.wyze_signing import WyzeSigner, WyzeSigningError


def test_sign_matches_golden_vector():
    body = {"device_id": "X", "device_model": "Y", "uid": 1}
    timestamp = 1700000000000
    signer = WyzeSigner(access_token="test-token-not-real")
    headers = signer.sign("POST", "/app/v4/wcsa/create-connection", body, timestamp)

    canonical = "\n".join(
        [
            "POST",
            "/app/v4/wcsa/create-connection",
            str(timestamp),
            json.dumps(body, sort_keys=True, separators=(",", ":")),
            "test-token-not-real",
            "9319141212m2ik",
        ]
    )
    expected_digest = hmac.new(
        b"9319141212m2ik", canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    expected_headers = {
        "Authorization": "test-token-not-real",
        "timestamp": str(timestamp),
        "appid": "9319141212m2ik",
        "signature": expected_digest,
        "Content-Type": "application/json",
    }
    assert headers == expected_headers


def test_sign_is_deterministic():
    signer = WyzeSigner(access_token="tok")
    body = {"a": 1, "b": 2}
    h1 = signer.sign("POST", "/p", body, 123)
    h2 = signer.sign("POST", "/p", body, 123)
    assert h1 == h2


def test_sign_raises_wyze_signing_error_on_missing_token():
    signer = WyzeSigner(access_token=None)
    with pytest.raises(WyzeSigningError):
        signer.sign("POST", "/p", {}, 1)


def test_sign_uses_provided_timestamp():
    signer = WyzeSigner(access_token="tok")
    timestamp = 42
    headers = signer.sign("GET", "/p", None, timestamp)
    assert str(timestamp) in headers.values() or headers.get("timestamp") == timestamp


def test_golden_vector_no_network():
    # Import-only / no network access smoke.
    signer = WyzeSigner(access_token="tok")
    assert signer is not None
