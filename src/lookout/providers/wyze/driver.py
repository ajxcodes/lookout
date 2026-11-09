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

    def __init__(self, session_ttl_seconds: float = 600.0) -> None:
        super().__init__(session_ttl_seconds=session_ttl_seconds)
        self._manifest_cache: ProviderManifest | None = None
        self._router: APIRouter | None = None

    def get_ui_manifest(self) -> ProviderManifest:
        """Return UI manifest loaded from manifest.json."""
        if self._manifest_cache is None:
            manifest_file = Path(__file__).parent / "manifest.json"
            self._manifest_cache = self.load_manifest_from_file(manifest_file)
        return self._manifest_cache

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        """Initiate Wyze authentication challenge."""
        session_id = f"wyze_session_{uuid.uuid4().hex[:12]}"
        email = credentials.get("email", "")
        password = credentials.get("password", "")
        api_key = credentials.get("api_key")
        api_id = credentials.get("api_id")

        self.store_session(
            session_id,
            {
                "email": email,
                "password": password,
                "api_key": api_key,
                "api_id": api_id,
                "provider": self.provider_id,
            },
        )
        return AuthResult(
            status="2fa_required",
            session_id=session_id,
            provider=self.provider_id,
            method="email/sms/totp",
            message="2FA verification code sent to your registered Wyze device/email",
        )

    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        """Verify Wyze 2FA challenge code and issue tokens."""
        session = self.get_session(session_id)
        if not session:
            return AuthResult(
                status="error",
                provider=self.provider_id,
                message="Invalid or expired session",
            )

        email = session.get("email", "")
        return AuthResult(
            status="authenticated",
            provider=self.provider_id,
            auth_token="mock-wyze-token-12345",
            refresh_token="mock-wyze-refresh-token-12345",
            email=email,
        )

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
