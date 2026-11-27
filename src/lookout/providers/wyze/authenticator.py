"""Lookout-owned Wyze login/2FA handshake.

The bundled ``wyze-sdk`` is used only as the HTTP transport/header layer
(``AuthServiceClient``).  The login handshake is owned by Lookout so the one-time
code the user already has (email, SMS, or authenticator app) is verified as a
*verification code* - never as a TOTP shared secret.  ``mintotp`` is never
imported and ``input()`` is never called.

Only an opaque issuer session handle and non-secret request metadata are
retained between ``begin_login`` and ``complete``; no password, token, or code
is logged or written to disk.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_EMAIL_ENDPOINT = "/v2/user/login/sendEmailCode"
_EMAIL_FALLBACK_ENDPOINT = "/api/user/login/email/mfa_sending_email"
_SMS_ENDPOINT = "/user/login/sendSmsCode"
_LOGIN_ENDPOINT = "/api/user/login"

_MAX_ATTEMPTS = 5


class WyzeLoginError(RuntimeError):
    """Base error for the Lookout-owned Wyze login handshake."""


class WyzeLoginConfigurationError(WyzeLoginError):
    """The issuer could not be constructed (missing dependency, bad config)."""


class WyzeLoginProtocolError(WyzeLoginError):
    """The Wyze response could not be mapped to a known 2FA method."""


class WyzeVerificationError(WyzeLoginError):
    """A code was rejected or the issuer session expired.

    ``retryable`` signals whether the caller may re-prompt for a code without
    restarting the credential entry step.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class WyzeChallenge:
    """A 2FA challenge the user can satisfy with a one-time code."""

    method: str
    wire_mfa_type: str
    verification_id: str | None = None
    destination: str | None = None


@dataclass(frozen=True)
class WyzeLoginStart:
    """Result of ``begin_login``: tokens (no 2FA) or a challenge (2FA)."""

    issuer_session_id: str | None
    tokens: dict[str, str] | None
    challenge: WyzeChallenge | None


@runtime_checkable
class WyzeTokenIssuer(Protocol):
    """Injectable boundary for issuing real Wyze access/refresh tokens."""

    def begin_login(
        self,
        email: str,
        password: str,
        key_id: str | None = None,
        api_key: str | None = None,
    ) -> WyzeLoginStart: ...
    def complete(self, session_id: str, code: str) -> dict[str, str]: ...
    def resend(self, session_id: str) -> str: ...
    def dispose(self, session_id: str) -> None: ...


def _response_get(response: Any, key: str, default: Any = None) -> Any:
    """Read a key from a dict-like transport response (``WyzeResponse``)."""
    getter = getattr(response, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def _response_keys(response: Any) -> list[str]:
    """Return response key names only - never values."""
    data = getattr(response, "data", response)
    if isinstance(data, dict):
        return sorted(str(key) for key in data)
    return []


def _triple_md5(password: str) -> str:
    """Reproduce the SDK/Wyze triple-MD5 password digest."""
    digest = password.encode("utf-8")
    for _ in range(3):
        digest = hashlib.md5(digest).hexdigest().encode("utf-8")
    return digest.decode("ascii")


def _nonce() -> str:
    return str(round(time.time() * 1000))


def _is_not_found(exc: Exception) -> bool:
    """Best-effort detection of a 404/405 transport failure for the fallback."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (404, 405):
        return True
    text = str(exc).lower()
    return "404" in text or "405" in text or "not found" in text or "method not allowed" in text


def _mask_email(email: str) -> str:
    """Mask an email address for display (``u***@example.com``)."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


class WyzeSdkIssuer:
    """Drive the Wyze login handshake over the ``wyze-sdk`` transport."""

    def __init__(
        self,
        transport_factory: Callable[[], Any] | None = None,
        session_ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport_factory = transport_factory
        self._transport: Any | None = None
        self._session_ttl_seconds = session_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _get_transport(self) -> Any:
        if self._transport is None:
            factory = self._transport_factory or self._default_transport_factory
            try:
                self._transport = factory()
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise WyzeLoginConfigurationError(
                    "wyze-sdk is not installed; install 'wyze-sdk>=2.3.16' to log in"
                ) from exc
        return self._transport

    @staticmethod
    def _default_transport_factory() -> Any:
        from wyze_sdk.service import AuthServiceClient  # type: ignore[import-untyped]

        return AuthServiceClient()

    # ------------------------------------------------------------------
    # Protocol primitives
    # ------------------------------------------------------------------
    @staticmethod
    def _auth_headers(key_id: str | None, api_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if key_id:
            headers["keyid"] = key_id
        if api_key:
            headers["apikey"] = api_key
        return headers

    def _post_login(
        self,
        email: str,
        password: str,
        *,
        key_id: str | None,
        api_key: str | None,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "email": email,
            "password": _triple_md5(password),
            "nonce": _nonce(),
        }
        if extra:
            payload.update(extra)
        return self._get_transport().api_call(
            _LOGIN_ENDPOINT,
            json=payload,
            request_specific_headers=self._auth_headers(key_id, api_key),
            nonce=_nonce(),
        )

    @staticmethod
    def _select_challenge(response: Any, email: str) -> WyzeChallenge:
        details = _response_get(response, "mfa_details") or {}
        primary = _response_get(details, "primary_option")
        options = _response_get(response, "mfa_options") or []
        candidate = primary or (options[0] if options else None)
        normalized = str(candidate).lower() if candidate else ""

        if "email" in normalized:
            destination = _response_get(details, "email") or _mask_email(email)
            return WyzeChallenge(
                method="email",
                wire_mfa_type="Email",
                destination=str(destination),
            )
        if "phone" in normalized or normalized in {"sms", "primaryphone"}:
            destination = _response_get(details, "phone")
            return WyzeChallenge(
                method="sms",
                wire_mfa_type="PrimaryPhone",
                destination=str(destination) if destination else None,
            )
        if "totp" in normalized:
            apps = _response_get(details, "totp_apps") or []
            app_id = None
            if apps:
                app_id = _response_get(apps[0], "app_id")
            return WyzeChallenge(
                method="totp",
                wire_mfa_type="TotpVerificationCode",
                verification_id=str(app_id) if app_id else None,
            )
        keys = ", ".join(_response_keys(response))
        raise WyzeLoginProtocolError(
            f"Wyze returned an unsupported 2FA challenge (response keys: {keys})"
        )

    def _send_email_code(self, session: dict[str, Any]) -> str | None:
        """Send the email code, with a single documented fallback on 404/405."""
        transport = self._get_transport()
        params = {
            key: value
            for key, value in {
                "userId": session.get("user_id"),
                "sessionId": session.get("email_session_id"),
            }.items()
            if value
        }
        try:
            response = transport.api_call(_EMAIL_ENDPOINT, params=params, json={})
        except Exception as exc:  # noqa: BLE001 - only 404/405 triggers the fallback
            if not _is_not_found(exc):
                raise
            response = transport.api_call(
                _EMAIL_FALLBACK_ENDPOINT,
                json={"email": session["email"], "user_id": session.get("user_id")},
            )
        session_id = _response_get(response, "session_id")
        return str(session_id) if session_id else None

    def _send_sms_code(self, session: dict[str, Any]) -> str | None:
        params = {
            key: value
            for key, value in {
                "mfaPhoneType": "Primary",
                "sessionId": session.get("sms_session_id"),
                "userId": session.get("user_id"),
            }.items()
            if value
        }
        response = self._get_transport().api_call(_SMS_ENDPOINT, params=params, json={})
        session_id = _response_get(response, "session_id")
        return str(session_id) if session_id else None

    # ------------------------------------------------------------------
    # Public issuer surface
    # ------------------------------------------------------------------
    def begin_login(
        self,
        email: str,
        password: str,
        key_id: str | None = None,
        api_key: str | None = None,
    ) -> WyzeLoginStart:
        """Start the handshake: return tokens directly or a 2FA challenge."""
        if not email or not password:
            raise WyzeLoginConfigurationError("Wyze login requires an email and password")

        response = self._post_login(email, password, key_id=key_id, api_key=api_key)
        access_token = _response_get(response, "access_token")
        if access_token:
            return WyzeLoginStart(
                issuer_session_id=None,
                tokens={
                    "access_token": str(access_token),
                    "refresh_token": str(_response_get(response, "refresh_token") or ""),
                },
                challenge=None,
            )

        challenge = self._select_challenge(response, email)
        issuer_session_id = f"wyze_issuer_{uuid.uuid4().hex[:12]}"
        session: dict[str, Any] = {
            "email": email,
            "password": password,
            "key_id": key_id,
            "api_key": api_key,
            "method": challenge.method,
            "wire_mfa_type": challenge.wire_mfa_type,
            "verification_id": challenge.verification_id,
            "destination": challenge.destination,
            "user_id": _response_get(response, "user_id"),
            "sms_session_id": _response_get(response, "sms_session_id"),
            "email_session_id": _response_get(response, "email_session_id"),
            "created_at": self._clock(),
            "attempts": 0,
        }

        if challenge.method == "email":
            session["verification_id"] = self._send_email_code(session)
        elif challenge.method == "sms":
            session["verification_id"] = self._send_sms_code(session)

        self._sessions[issuer_session_id] = session
        return WyzeLoginStart(
            issuer_session_id=issuer_session_id,
            tokens=None,
            challenge=WyzeChallenge(
                method=challenge.method,
                wire_mfa_type=challenge.wire_mfa_type,
                verification_id=session.get("verification_id"),
                destination=challenge.destination,
            ),
        )

    def _active_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise WyzeVerificationError(
                "The Wyze login session expired; start the login again.",
                retryable=False,
            )
        if self._clock() - float(session.get("created_at", 0.0)) > self._session_ttl_seconds:
            self.dispose(session_id)
            raise WyzeVerificationError(
                "The Wyze login session expired; start the login again.",
                retryable=False,
            )
        return session

    def complete(self, session_id: str, code: str) -> dict[str, str]:
        """Verify the one-time code and return the issued token pair."""
        session = self._active_session(session_id)
        if int(session.get("attempts", 0)) >= _MAX_ATTEMPTS:
            self.dispose(session_id)
            raise WyzeVerificationError(
                "Too many incorrect codes; start the login again.", retryable=False
            )

        session["attempts"] = int(session.get("attempts", 0)) + 1
        extra: dict[str, Any] = {
            "mfa_type": session["wire_mfa_type"],
            "verification_code": str(code),
        }
        if session.get("verification_id"):
            extra["verification_id"] = session["verification_id"]

        try:
            response = self._post_login(
                str(session["email"]),
                str(session["password"]),
                key_id=session.get("key_id"),
                api_key=session.get("api_key"),
                extra=extra,
            )
        except Exception as exc:  # noqa: BLE001 - retryable until attempts run out
            if int(session.get("attempts", 0)) >= _MAX_ATTEMPTS:
                self.dispose(session_id)
                raise WyzeVerificationError(
                    "Too many incorrect codes; start the login again.",
                    retryable=False,
                ) from exc
            raise WyzeVerificationError(
                "That code was not accepted. Enter the current code and try again."
            ) from exc

        access_token = _response_get(response, "access_token")
        refresh_token = _response_get(response, "refresh_token")
        if not access_token or not refresh_token:
            if int(session.get("attempts", 0)) >= _MAX_ATTEMPTS:
                self.dispose(session_id)
                raise WyzeVerificationError(
                    "Too many incorrect codes; start the login again.",
                    retryable=False,
                )
            raise WyzeVerificationError(
                "That code was not accepted. Enter the current code and try again."
            )

        self.dispose(session_id)
        return {
            "access_token": str(access_token),
            "refresh_token": str(refresh_token),
        }

    def resend(self, session_id: str) -> str:
        """Re-send an email/SMS code without reopening the credential modal."""
        session = self._active_session(session_id)
        method = session.get("method")
        if method == "email":
            session["verification_id"] = self._send_email_code(session)
            destination = session.get("destination")
            return (
                f"Wyze emailed a new code to {destination}."
                if destination
                else "Wyze emailed a new code."
            )
        if method == "sms":
            session["verification_id"] = self._send_sms_code(session)
            return "Wyze texted a new code to your phone."
        if method == "totp":
            return "Use the current code shown in your authenticator app."
        raise WyzeLoginProtocolError("Unsupported Wyze 2FA method for resend")

    def dispose(self, session_id: str) -> None:
        """Drop a staged session immediately after use."""
        self._sessions.pop(session_id, None)
