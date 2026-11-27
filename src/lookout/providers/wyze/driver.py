"""Self-contained Wyze provider driver implementing BaseProviderDriver."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lookout.config.schema import CameraConfig, LookoutConfig
from lookout.providers.base import (
    AuthResult,
    BaseProviderDriver,
    DiscoveryResult,
    ProviderManifest,
    StreamDescriptor,
)
from lookout.providers.credential_probe import wyze_credentials_present
from lookout.providers.wyze.authenticator import WyzeSdkIssuer, WyzeTokenIssuer
from lookout.providers.wyze.discovery import probe_wyze
from lookout.providers.wyze.routes import create_wyze_router
from lookout.providers.wyze.sync import sync_wyze_environment

if TYPE_CHECKING:
    from fastapi import APIRouter


class WyzeProviderDriver(BaseProviderDriver):
    """Driver encapsulating Wyze camera bridge authentication, discovery, and sync."""

    provider_id: str = "wyze"
    display_name: str = "Wyze Cam Bridge"
    default_ports: dict[str, int] = {"http": 5000, "rtsp": 8554}

    def __init__(
        self,
        session_ttl_seconds: float = 600.0,
        token_issuer: WyzeTokenIssuer | None = None,
    ) -> None:
        super().__init__(session_ttl_seconds=session_ttl_seconds)
        self._manifest_cache: ProviderManifest | None = None
        self._router: APIRouter | None = None
        self._token_issuer: WyzeTokenIssuer | None = token_issuer

    @property
    def token_issuer(self) -> WyzeTokenIssuer:
        """Lazily construct the real SDK-backed issuer."""
        if self._token_issuer is None:
            self._token_issuer = WyzeSdkIssuer()
        return self._token_issuer

    def set_token_issuer(self, issuer: WyzeTokenIssuer | None) -> None:
        """Inject an issuer (used by tests and the auth service)."""
        self._token_issuer = issuer

    def get_ui_manifest(self) -> ProviderManifest:
        """Return UI manifest loaded from manifest.json."""
        if self._manifest_cache is None:
            manifest_file = Path(__file__).parent / "manifest.json"
            self._manifest_cache = self.load_manifest_from_file(manifest_file)
        return self._manifest_cache

    @staticmethod
    def _challenge_message(method: str, destination: str | None) -> str:
        if method == "email":
            if destination:
                return f"Wyze emailed a code to {destination}. Enter it below."
            return "Wyze emailed a verification code. Enter it below."
        if method == "sms":
            return "Wyze texted a code to your phone. Enter it below."
        return "Enter the current 6-digit code from your authenticator app."

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        """Begin the Lookout-owned Wyze login, returning tokens or a challenge."""
        email = str(credentials.get("email", ""))
        password = str(credentials.get("password", ""))
        api_key = credentials.get("api_key")
        api_id = credentials.get("api_id") or credentials.get("key_id")
        try:
            start = self.token_issuer.begin_login(
                email,
                password,
                key_id=str(api_id) if api_id else None,
                api_key=str(api_key) if api_key else None,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as an error state
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message=f"Wyze login could not be started: {exc}",
            )

        if start.tokens:
            return AuthResult(
                status="authenticated",
                provider=self.provider_id,
                auth_token=start.tokens.get("access_token"),
                refresh_token=start.tokens.get("refresh_token"),
                email=email,
            )

        if start.challenge is None or start.issuer_session_id is None:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Wyze login returned neither tokens nor a 2FA challenge",
            )

        session_id = f"wyze_session_{uuid.uuid4().hex[:12]}"
        self.store_session(
            session_id,
            {
                "email": email,
                "issuer_session_id": start.issuer_session_id,
                "method": start.challenge.method,
                "destination": start.challenge.destination,
                "provider": self.provider_id,
            },
        )
        return AuthResult(
            status="2fa_required",
            session_id=session_id,
            provider=self.provider_id,
            method=start.challenge.method,
            message=self._challenge_message(start.challenge.method, start.challenge.destination),
        )

    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        """Verify the one-time code and return real tokens."""
        session = self.get_session(session_id)
        if not session:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Invalid or expired session",
            )
        issuer_session_id = session.get("issuer_session_id")
        if not issuer_session_id:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Invalid or expired issuer session",
            )
        try:
            tokens = self.token_issuer.complete(str(issuer_session_id), code)
        except Exception as exc:  # noqa: BLE001 - surfaced as an error state
            retryable = bool(getattr(exc, "retryable", False))
            if retryable:
                return AuthResult(
                    status="2fa_required",
                    session_id=session_id,
                    provider=self.provider_id,
                    method=session.get("method"),
                    message=f"Wyze 2FA verification failed: {exc}",
                )
            self.token_issuer.dispose(str(issuer_session_id))
            self._sessions.pop(session_id, None)
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message=f"Wyze 2FA verification failed: {exc}",
            )
        self.token_issuer.dispose(str(issuer_session_id))
        self._sessions.pop(session_id, None)

        return AuthResult(
            status="authenticated",
            provider=self.provider_id,
            auth_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            email=session.get("email", ""),
        )

    def resend_2fa(self, session_id: str) -> AuthResult:
        """Re-send the email/SMS code for an active challenge."""
        session = self.get_session(session_id)
        if not session:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Invalid or expired session",
            )
        issuer_session_id = session.get("issuer_session_id")
        if not issuer_session_id:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Invalid or expired issuer session",
            )
        try:
            message = self.token_issuer.resend(str(issuer_session_id))
        except Exception as exc:  # noqa: BLE001 - surfaced as an error state
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message=f"Wyze code could not be resent: {exc}",
            )
        return AuthResult(
            status="2fa_required",
            session_id=session_id,
            provider=self.provider_id,
            method=session.get("method"),
            message=message,
        )

    def has_valid_credentials(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Delegate offline credential discovery to the shared probe."""
        return wyze_credentials_present(config)

    def dispose_session(self, session_id: str) -> None:
        """Dispose any issuer session referenced by a driver session."""
        session = self._sessions.pop(session_id, None)
        if session and session.get("issuer_session_id"):
            self.token_issuer.dispose(str(session["issuer_session_id"]))

    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult:
        """Probe for existing Wyze bridge instances or configuration."""
        return probe_wyze(self, config)

    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]:
        """Generate wyze-bridge.env sidecar file."""
        return sync_wyze_environment(config, target_dir)

    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]:
        """Return resolvable Wyze stream descriptors."""
        if config is not None and config.cameras:
            wyze_cams = [c for c in config.cameras if c.camera_type == "wyze"]
            if wyze_cams:
                return [
                    StreamDescriptor(
                        stream_url=c.stream_url,
                        camera_id=c.id,
                        camera_name=c.name,
                    )
                    for c in wyze_cams
                ]
        return [
            StreamDescriptor(
                stream_url="rtsp://localhost:8554/cam1",
                camera_id="wyze_cam1",
                camera_name="Wyze Cam 1",
            )
        ]

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt stream as wyze camera type."""
        cam = CameraConfig(
            id=camera_id,
            name=camera_name,
            stream_url=stream_url,
            camera_type="wyze",
        )
        if config is not None:
            existing_idx = next(
                (i for i, c in enumerate(config.cameras) if c.id == camera_id), None
            )
            if existing_idx is not None:
                config.cameras[existing_idx] = cam
            else:
                config.cameras.append(cam)
        return cam

    def get_router(self) -> APIRouter | None:
        """Return Wyze-specific REST sub-router."""
        if self._router is None:
            self._router = create_wyze_router(self)
        return self._router
