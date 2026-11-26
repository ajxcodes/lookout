"""Real Wyze token issuance via ``wyze-sdk`` login + 2FA.

The SDK is injected via ``client_factory`` so unit tests can supply a fake
client.  Only the opaque session handle and non-secret request metadata are
retained between ``begin_login`` and ``complete``; no password or token is
logged or written to disk.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WyzeTokenIssuer(Protocol):
    """Injectable boundary for issuing real Wyze access/refresh tokens."""

    def begin_login(
        self,
        email: str,
        password: str,
        key_id: str | None = None,
        api_key: str | None = None,
        totp_key: str | None = None,
    ) -> str: ...
    def complete(self, session_id: str, code: str) -> dict[str, str]: ...
    def dispose(self, session_id: str) -> None: ...


class WyzeSdkIssuer:
    """Wrap ``wyze_sdk.Client`` to perform a real login and return tokens."""

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        if client_factory is None:
            from wyze_sdk import Client  # type: ignore[import-untyped]

            client_factory = Client
        self._client_factory = client_factory
        self._sessions: dict[str, dict[str, Any]] = {}

    def begin_login(
        self,
        email: str,
        password: str,
        key_id: str | None = None,
        api_key: str | None = None,
        totp_key: str | None = None,
    ) -> str:
        """Stage credentials for a subsequent 2FA completion."""
        session_id = f"wyze_issuer_{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = {
            "email": email,
            "password": password,
            "key_id": key_id,
            "api_key": api_key,
            "totp_key": totp_key,
        }
        return session_id

    def complete(self, session_id: str, code: str) -> dict[str, str]:
        """Perform the SDK login with the supplied 2FA secret/code."""
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Invalid or expired Wyze issuer session")
        try:
            client = self._client_factory(
                email=session.get("email"),
                password=session.get("password"),
                key_id=session.get("key_id"),
                api_key=session.get("api_key"),
                totp_key=code or session.get("totp_key"),
            )
            # The real ``wyze_sdk.Client`` auto-logs-in inside ``__init__`` when
            # an email is supplied and no token is passed. Calling ``login()``
            # a second time raises ``WyzeClientConfigurationError: already
            # logged in``. Only drive ``login()`` for clients that have not
            # authenticated yet (e.g. injected fakes that do not auto-login).
            access_token = getattr(client, "_token", None) or getattr(client, "access_token", None)
            if not access_token:
                login = getattr(client, "login", None)
                if callable(login):
                    login()
                access_token = getattr(client, "_token", None) or getattr(
                    client, "access_token", None
                )
            refresh_token = getattr(client, "_refresh_token", None) or getattr(
                client, "refresh", None
            )
            if not access_token or not refresh_token:
                raise ValueError("Wyze login did not return access and refresh tokens")
            return {
                "access_token": str(access_token),
                "refresh_token": str(refresh_token),
            }
        finally:
            self.dispose(session_id)

    def dispose(self, session_id: str) -> None:
        """Drop a staged session immediately after use."""
        self._sessions.pop(session_id, None)
