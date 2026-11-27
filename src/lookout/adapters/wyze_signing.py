"""Wyze ``signature2`` request signing for wcsa/device-management calls.

The scheme mirrors the pinned ``wyze_sdk`` ``RequestVerifier`` /
``ExServiceClient`` implementation used by the official app:

- ``appid`` = ``9319141212m2ik`` with salt ``wyze_app_secret_key_132``;
- ``nonce`` is inserted into the POST body *before* serialization;
- ``requestid`` = ``md5(md5(nonce))``;
- ``signature2`` = ``HMAC-MD5(key = md5(access_token + salt), message = body)``.

The legacy reconstructed canonical-string scheme is kept behind
``scheme="legacy"`` for live bisection only. Signing and serialization travel
together through :class:`SignedRequest` so the adapter sends exactly the bytes
that were signed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SignedRequest",
    "WYZE_ANDROID_APP_VERSION",
    "WYZE_APP_ID",
    "WYZE_APP_SECRET_SALT",
    "WyzeSigner",
    "WyzeSigningError",
]

WYZE_APP_ID = "9319141212m2ik"
WYZE_APP_SECRET_SALT = "wyze_app_secret_key_132"
WYZE_ANDROID_APP_VERSION = "2.19.14"

_APP_KEY_DEFAULT = WYZE_APP_ID


class WyzeSigningError(RuntimeError):
    """Raised when signing cannot be performed (missing token, bad body, etc.)."""


@dataclass(frozen=True)
class SignedRequest:
    """The exact headers and body bytes of one signed Wyze request."""

    headers: dict[str, str]
    body: bytes
    nonce: str


class WyzeSigner:
    """Produce the signed headers/body for a single Wyze cloud request."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        app_key: str = _APP_KEY_DEFAULT,
        app_version: str = WYZE_ANDROID_APP_VERSION,
        phone_id: str | None = None,
        scheme: str = "signature2",
        user_agent: str | None = None,
        locale: str = "en",
        device_id: str | None = None,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.app_id = app_key
        self.app_key = app_key
        self.app_version = app_version
        self.phone_id = phone_id or str(uuid.uuid4())
        self.scheme = scheme
        self.user_agent = user_agent
        self.locale = locale
        self.device_id = device_id

    # ------------------------------------------------------------------
    # Canonicalization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical_body(body: Any) -> str:
        if body is None:
            return ""
        if isinstance(body, (dict, list)):
            try:
                return json.dumps(body, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise WyzeSigningError(f"Cannot canonicalize body: {exc}") from exc
        if isinstance(body, bytes):
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WyzeSigningError(f"Cannot decode body bytes: {exc}") from exc
        if isinstance(body, str):
            return body
        raise WyzeSigningError(f"Unsupported body type: {type(body).__name__}")

    @staticmethod
    def _normalize_timestamp(timestamp: Any) -> int:
        if timestamp is None:
            return round(time.time() * 1000)
        if isinstance(timestamp, bool):
            raise WyzeSigningError("timestamp must not be a bool")
        if isinstance(timestamp, int):
            return timestamp
        if isinstance(timestamp, float):
            return int(timestamp)
        if isinstance(timestamp, str):
            stripped = timestamp.strip()
            try:
                return int(stripped)
            except ValueError:
                try:
                    return int(float(stripped))
                except ValueError as exc:
                    raise WyzeSigningError(f"Cannot parse timestamp: {timestamp!r}") from exc
        raise WyzeSigningError(f"Unsupported timestamp type: {type(timestamp).__name__}")

    @staticmethod
    def _md5(value: str) -> str:
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _compact_bytes(payload: Any) -> bytes:
        try:
            return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WyzeSigningError(f"Cannot serialize body: {exc}") from exc

    def _ex_service_headers(self) -> dict[str, str]:
        appinfo = f"wyze_android_{self.app_version}"
        return {
            "appid": self.app_id,
            "appinfo": appinfo,
            "phoneid": self.phone_id,
            "User-Agent": f"wyze_android_{self.app_version}",
        }

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------
    def sign_request(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        nonce: str | int | None = None,
        timestamp: Any = None,
    ) -> SignedRequest:
        """Return the signed headers and exact body bytes for a request."""
        if not self.access_token:
            raise WyzeSigningError("access_token is required to sign a Wyze request")

        method_norm = str(method).upper().strip()
        if not method_norm:
            raise WyzeSigningError("method is required")

        if self.scheme == "legacy":
            return self._sign_legacy(method_norm, path, body, timestamp)
        return self._sign_signature2(body, nonce=nonce, timestamp=timestamp)

    def _sign_signature2(
        self,
        body: Any,
        *,
        nonce: str | int | None,
        timestamp: Any,
    ) -> SignedRequest:
        token = self.access_token
        if not token:
            raise WyzeSigningError("access_token is required to sign a Wyze request")
        payload: Any = dict(body) if isinstance(body, dict) else body
        if payload is None:
            payload = {}
        if isinstance(payload, dict):
            existing = payload.get("nonce")
            if existing is not None:
                nonce_str = str(existing)
            else:
                nonce_hint = nonce if nonce is not None else timestamp
                nonce_str = (
                    str(nonce_hint) if nonce_hint is not None else str(round(time.time() * 1000))
                )
                payload["nonce"] = nonce_str
            body_bytes = self._compact_bytes(payload)
        else:
            nonce_hint = nonce if nonce is not None else timestamp
            nonce_str = (
                str(nonce_hint) if nonce_hint is not None else str(round(time.time() * 1000))
            )
            body_bytes = self._compact_bytes(payload)

        request_id = self._md5(self._md5(nonce_str))
        derived_key = self._md5(f"{token}{WYZE_APP_SECRET_SALT}")
        try:
            signature = hmac.new(derived_key.encode("utf-8"), body_bytes, hashlib.md5).hexdigest()
        except Exception as exc:  # noqa: BLE001 - reported as signing error
            raise WyzeSigningError(f"HMAC computation failed: {exc}") from exc

        headers: dict[str, str] = {
            "Authorization": token,
            "access_token": token,
            "signature2": signature,
            "requestid": request_id,
            "Content-Type": "application/json;charset=utf-8",
        }
        headers.update(self._ex_service_headers())
        return SignedRequest(headers=headers, body=body_bytes, nonce=nonce_str)

    def _sign_legacy(self, method_norm: str, path: str, body: Any, timestamp: Any) -> SignedRequest:
        ts_int = self._normalize_timestamp(timestamp)
        body_canonical = self._canonical_body(body)
        path_norm = str(path)

        # Reconstructed canonical string. Kept for live bisection only.
        canonical = "\n".join(
            [
                method_norm,
                path_norm,
                str(ts_int),
                body_canonical,
                self.access_token or "",
                self.app_key,
            ]
        )
        try:
            digest = hmac.new(
                self.app_key.encode("utf-8"),
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        except Exception as exc:  # noqa: BLE001 - reported as signing error
            raise WyzeSigningError(f"HMAC computation failed: {exc}") from exc

        headers: dict[str, str] = {
            "Authorization": self.access_token or "",
            "timestamp": str(ts_int),
            "appid": self.app_key,
            "signature": digest,
            "Content-Type": "application/json",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.device_id:
            headers["device-id"] = self.device_id
        return SignedRequest(headers=headers, body=body_canonical.encode("utf-8"), nonce="")

    def sign(self, method: str, path: str, body: Any, timestamp: Any = None) -> dict[str, str]:
        """Compatibility wrapper returning only the signed headers."""
        return self.sign_request(method, path, body, timestamp=timestamp).headers

    def verify(self, method: str, path: str, body: Any, timestamp: Any, expected: str) -> bool:
        """Compare a produced signature against an expected digest."""
        headers = self.sign_request(method, path, body, timestamp=timestamp).headers
        digest = headers.get("signature2") if self.scheme != "legacy" else headers.get("signature")
        return hmac.compare_digest(digest or "", expected)
