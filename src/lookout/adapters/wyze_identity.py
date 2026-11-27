"""Decode the Wyze account id from an access-token JWT.

The Wyze access token is a JWT whose payload carries the account ``user_id``
the device-management API expects in ``userId``. Decoding the payload is a
convenience for a token the server issued; it is not cryptographic
verification and must never be used as an authentication decision by itself.
"""

from __future__ import annotations

import base64
import json

__all__ = ["decode_jwt_user_id"]

_CLAIM_NAMES = ("user_id", "userId", "sub")


def decode_jwt_user_id(access_token: str | None) -> str | None:
    """Return the first non-empty account-id claim of ``access_token``.

    Returns ``None`` for a missing, opaque, or malformed token. Never raises
    and never logs the token.
    """
    if not access_token or not isinstance(access_token, str):
        return None
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    if not payload:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(decoded)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(claims, dict):
        return None
    for name in _CLAIM_NAMES:
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return None
