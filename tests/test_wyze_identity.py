import base64

from lookout.adapters.wyze_identity import decode_jwt_user_id


def test_decode_jwt_user_id_from_access_token():
    # Literal fake header/payload/signature; no real token material.
    payload = base64.urlsafe_b64encode(b'{"user_id": "user-123"}').decode().rstrip("=")
    jwt = f"test-header.{payload}.test-signature"

    assert decode_jwt_user_id(jwt) == "user-123"
    # The helper never raises, even for arbitrary input.
    assert decode_jwt_user_id("") is None


def test_decode_jwt_user_id_returns_none_for_malformed_token():
    assert decode_jwt_user_id("not-a-jwt") is None
    assert decode_jwt_user_id(None) is None
    assert decode_jwt_user_id("a.b.c") is None
