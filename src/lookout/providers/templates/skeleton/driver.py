"""Starter template driver implementation with explanatory docstrings."""

from __future__ import annotations

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
from lookout.providers.templates.skeleton.discovery import probe_skeleton
from lookout.providers.templates.skeleton.routes import create_skeleton_router
from lookout.providers.templates.skeleton.sync import sync_skeleton_environment

if TYPE_CHECKING:
    from fastapi import APIRouter


class SkeletonProviderDriver(BaseProviderDriver):
    """Starter skeleton driver for integrating new camera brands into Lookout.

    To implement a new camera ecosystem (e.g. Eufy, Tapo, Blink, Reolink):
    1. Copy this folder into `src/lookout/providers/<brand_name>/`
    2. Update `manifest.json` with your brand's display name, ports, and credentials schema
    3. Implement `authenticate` and `verify_2fa` in `driver.py`
    4. Implement sidecar environment generation in `sync.py`
    5. Implement connection probing in `discovery.py`
    """

    provider_id: str = "skeleton"
    display_name: str = "Starter Template Driver"
    default_ports: dict[str, int] = {"http": 8080, "rtsp": 8554}

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
        """Authenticate with provider or trigger 2FA."""
        username = credentials.get("username") or credentials.get("email") or "admin"
        return AuthResult(
            status="authenticated",
            provider=self.provider_id,
            auth_token="mock-skeleton-token",
            email=str(username),
        )

    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        """Verify 2FA challenge code and obtain tokens."""
        return AuthResult(
            status="authenticated",
            provider=self.provider_id,
            auth_token="mock-skeleton-token",
        )

    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult:
        """Probe for existing bridge instances or configuration."""
        return probe_skeleton(self, config)

    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]:
        """Generate sidecar environment file."""
        return sync_skeleton_environment(config, target_dir)

    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]:
        """Return resolvable stream descriptors."""
        return [
            StreamDescriptor(
                stream_url="rtsp://localhost:8554/live",
                camera_id="skeleton_cam1",
                camera_name="Skeleton Camera",
            )
        ]

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt stream as skeleton camera type."""
        cam = CameraConfig(
            id=camera_id,
            name=camera_name,
            stream_url=stream_url,
            camera_type=self.provider_id,
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
        """Return skeleton REST sub-router."""
        if self._router is None:
            self._router = create_skeleton_router(self)
        return self._router
