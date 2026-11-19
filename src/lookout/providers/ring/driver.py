"""Self-contained Ring provider driver implementing BaseProviderDriver."""

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
from lookout.providers.credential_probe import ring_credentials_present
from lookout.providers.ring.authenticator import RingAuthCliIssuer, RingTokenIssuer
from lookout.providers.ring.discovery import probe_ring
from lookout.providers.ring.routes import create_ring_router
from lookout.providers.ring.sync import sync_ring_environment

if TYPE_CHECKING:
    from fastapi import APIRouter


class RingProviderDriver(BaseProviderDriver):
    """Driver encapsulating Ring Video Doorbell authentication, discovery, and sync."""

    provider_id: str = "ring"
    display_name: str = "Ring Video Doorbell"
    default_ports: dict[str, int] = {"http": 55123, "rtsp": 8555}

    def __init__(
        self,
        session_ttl_seconds: float = 600.0,
        token_issuer: RingTokenIssuer | None = None,
    ) -> None:
        super().__init__(session_ttl_seconds=session_ttl_seconds)
        self._manifest_cache: ProviderManifest | None = None
        self._router: APIRouter | None = None
        self._token_issuer: RingTokenIssuer | None = token_issuer

    @property
    def token_issuer(self) -> RingTokenIssuer:
        """Lazily construct the real CLI-backed issuer (no I/O at discovery)."""
        if self._token_issuer is None:
            self._token_issuer = RingAuthCliIssuer()
        return self._token_issuer

    def set_token_issuer(self, issuer: RingTokenIssuer | None) -> None:
        """Inject an issuer (used by tests and the auth service)."""
        self._token_issuer = issuer

    def get_ui_manifest(self) -> ProviderManifest:
        """Return UI manifest loaded from manifest.json."""
        if self._manifest_cache is None:
            manifest_file = Path(__file__).parent / "manifest.json"
            self._manifest_cache = self.load_manifest_from_file(manifest_file)
        return self._manifest_cache

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        """Begin a real Ring login via the issuer, returning a 2FA challenge."""
        email = str(credentials.get("email", ""))
        password = str(credentials.get("password", ""))
        try:
            issuer_session_id = self.token_issuer.begin_login(email, password)
        except Exception as exc:  # noqa: BLE001 - surfaced as an error state
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message=f"Ring login could not be started: {exc}",
            )

        session_id = f"ring_session_{uuid.uuid4().hex[:12]}"
        # Only the email and an opaque issuer handle are retained; the password
        # lives solely in the issuer session and is dropped after completion.
        self.store_session(
            session_id,
            {
                "email": email,
                "issuer_session_id": issuer_session_id,
                "provider": self.provider_id,
            },
        )
        return AuthResult(
            status="2fa_required",
            session_id=session_id,
            provider=self.provider_id,
            phone="***-***-1234",
            message="Enter the 2FA code sent to your registered phone",
        )

    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        """Complete the issuer 2FA exchange and return the real RINGTOKEN."""
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
            raw_token = self.token_issuer.complete(str(issuer_session_id), code)
        except Exception as exc:  # noqa: BLE001 - surfaced as an error state
            self.token_issuer.dispose(str(issuer_session_id))
            self._sessions.pop(session_id, None)
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message=f"Ring 2FA verification failed: {exc}",
            )
        self.token_issuer.dispose(str(issuer_session_id))
        self._sessions.pop(session_id, None)

        email = session.get("email", "")
        return AuthResult(
            status="authenticated",
            provider=self.provider_id,
            refresh_token=raw_token,
            auth_token=raw_token,
            email=email,
        )

    def has_valid_credentials(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Delegate offline credential discovery to the shared probe."""
        return ring_credentials_present(config)

    def dispose_session(self, session_id: str) -> None:
        """Dispose any issuer session referenced by a driver session."""
        session = self._sessions.pop(session_id, None)
        if session and session.get("issuer_session_id"):
            self.token_issuer.dispose(str(session["issuer_session_id"]))

    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult:
        """Probe for existing Ring token or ring-mqtt bridge."""
        return probe_ring(self, config)

    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]:
        """Generate ring-mqtt.env sidecar file."""
        return sync_ring_environment(config, target_dir)

    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]:
        """Return resolvable Ring stream descriptors."""
        if config is not None and config.cameras:
            ring_cams = [c for c in config.cameras if c.camera_type == "ring"]
            if ring_cams:
                return [
                    StreamDescriptor(
                        stream_url=c.stream_url,
                        camera_id=c.id,
                        camera_name=c.name,
                    )
                    for c in ring_cams
                ]
        return [
            StreamDescriptor(
                stream_url="rtsp://localhost:8555/live",
                camera_id="ring_doorbell_1",
                camera_name="Ring Doorbell",
            )
        ]

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt stream as ring camera type."""
        cam = CameraConfig(
            id=camera_id,
            name=camera_name,
            stream_url=stream_url,
            camera_type="ring",
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
        """Return Ring-specific REST sub-router."""
        if self._router is None:
            self._router = create_ring_router(self)
        return self._router
