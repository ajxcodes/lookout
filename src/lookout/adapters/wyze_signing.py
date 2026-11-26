"""Wyze "dynamic signature" reconstruction for wcsa/device-management calls.

This module reproduces the signing scheme used by Wyze's official app
(``WpkWyzeExService`` / ``WpkSignature2Service`` with app key
``9319141212m2ik``) so Lookout can call the cloud endpoints the app uses for
device capabilities and wcsa connection parameters, without the Agora media
stack.

The canonical string / HMAC below is a *reconstructed* scheme. No captured
request/response pair is available in-repository (capture is an on-device step
per the issue); the scheme is locked by synthetic golden vectors until a
captured sample is available, at which point the canonicalization must be
updated to match byte-for-byte.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

__all__ = ["WyzeSigningError", "WyzeSigner"]

_APP_KEY_DEFAULT = "9319141212m2ik"


class WyzeSigningError(RuntimeError):
    """Raised when signing cannot be performed (missing token, bad body, etc.)."""


class WyzeSigner:
    """Produce the dynamic-signature headers for a single Wyze cloud request."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        app_key: str = _APP_KEY_DEFAULT,
        user_agent: str | None = None,
        app_version: str | None = None,
        locale: str = "en",
        device_id: str | None = None,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.app_key = app_key
        self.user_agent = user_agent
        self.app_version = app_version
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

    def sign(self, method: str, path: str, body: Any, timestamp: Any) -> dict[str, str]:
        """Return the header dict for a request.

        ``method``/``path``/``body``/``timestamp`` describe the outgoing request.
        Raises :class:`WyzeSigningError` when no access token is configured or
        the body cannot be canonicalized.
        """
        if not self.access_token:
            raise WyzeSigningError("access_token is required to sign a Wyze request")

        method_norm = str(method).upper().strip()
        if not method_norm:
            raise WyzeSigningError("method is required")
        path_norm = str(path)
        ts_int = self._normalize_timestamp(timestamp)
        body_canonical = self._canonical_body(body)

        # Reconstructed canonical string. Update once a captured sample lands.
        canonical = "\n".join(
            [
                method_norm,
                path_norm,
                str(ts_int),
                body_canonical,
                self.access_token,
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
            "Authorization": self.access_token,
            "timestamp": str(ts_int),
            "appid": self.app_key,
            "signature": digest,
            "Content-Type": "application/json",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.app_version:
            headers["app-version"] = self.app_version
        if self.device_id:
            headers["device-id"] = self.device_id
        return headers

    def verify(self, method: str, path: str, body: Any, timestamp: Any, expected: str) -> bool:
        """Compare a produced signature against an expected digest."""
        headers = self.sign(method, path, body, timestamp)
        return hmac.compare_digest(headers.get("signature", ""), expected)
