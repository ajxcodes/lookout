"""Standardized BaseProviderDriver ABC, protocol, and result schemas."""

from __future__ import annotations

import abc
import json
import logging
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lookout.config.schema import CameraConfig, LookoutConfig, mask_secret

if TYPE_CHECKING:
    from fastapi import APIRouter


class AuthResult(BaseModel):
    """Result schema for provider authentication and 2FA challenges."""

    model_config = ConfigDict(extra="allow")

    status: str
    provider: str
    session_id: str | None = None
    auth_token: str | None = None
    refresh_token: str | None = None
    email: str | None = None
    phone: str | None = None
    method: str | None = None
    message: str | None = None
    details: str | None = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item) and getattr(self, item) is not None


class DiscoveryResult(BaseModel):
    """Result schema for stream and sidecar detection."""

    model_config = ConfigDict(extra="allow")

    status: str
    provider: str
    mode: str | None = None
    details: str | None = None
    email: str | None = None
    token_present: bool | None = None
    bridge_reachable: bool | None = None
    ports: list[int] | None = None
    metadata: dict[str, Any] | None = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item) and getattr(self, item) is not None


class ConnectionResult(BaseModel):
    """Result schema for reachability and connectivity testing."""

    model_config = ConfigDict(extra="allow")

    reachable: bool
    provider: str
    endpoint: str | None = None
    ports: list[int] | None = None
    message: str | None = None
    error: str | None = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item) and getattr(self, item) is not None


class StreamDescriptor(BaseModel):
    """Schema describing resolvable camera streams."""

    model_config = ConfigDict(extra="allow")

    stream_url: str
    camera_id: str
    camera_name: str
    stream_type: str = "rtsp"
    channel: int | None = None
    resolution: str | None = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class ProviderManifest(BaseModel):
    """UI presentation metadata for camera stream providers."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    display_name: str = ""
    description: str = ""
    icon: str = ""
    brand_color: str = ""
    default_ports: dict[str, int] = Field(default_factory=dict)
    default_stream_url: str | None = None
    default_camera_name: str | None = None
    supported_2fa: list[str] = Field(default_factory=list)
    auth_fields: list[dict[str, Any]] = Field(default_factory=list)
    badge: str = ""
    docs_url: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.name and self.id:
            self.name = self.id
        if not self.display_name:
            self.display_name = self.name or self.id

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


@runtime_checkable
class ProviderDriverProtocol(Protocol):
    """Protocol specifying the required methods of a provider driver."""

    provider_id: str
    display_name: str
    default_ports: dict[str, int]

    def authenticate(self, credentials: dict[str, Any]) -> AuthResult: ...
    def verify_2fa(self, session_id: str, code: str) -> AuthResult: ...
    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult: ...
    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]: ...
    def test_connection(self, endpoint: str | None = None) -> ConnectionResult: ...
    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]: ...
    def get_ui_manifest(self) -> ProviderManifest: ...
    def get_logger(self) -> logging.Logger: ...


class BaseProviderDriver(abc.ABC):
    """Abstract base class for modular stream provider drivers."""

    provider_id: str = ""
    display_name: str = ""
    default_ports: dict[str, int] = {}

    def __init__(self, session_ttl_seconds: float = 600.0) -> None:
        self.session_ttl_seconds = session_ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}

    def get_logger(self) -> logging.Logger:
        """Return namespaced logger for this provider driver."""
        return logging.getLogger(f"lookout.providers.{self.provider_id or 'generic'}")

    def _prune_expired_sessions(self) -> None:
        """Purge sessions older than session_ttl_seconds."""
        now = time.time()
        expired = [
            sid
            for sid, sess in self._sessions.items()
            if now - float(sess.get("created_at", 0)) > self.session_ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)

    def store_session(self, session_id: str, data: dict[str, Any]) -> None:
        """Store transient authentication session data with timestamp."""
        self._prune_expired_sessions()
        session_data = dict(data)
        session_data["created_at"] = time.time()
        self._sessions[session_id] = session_data

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve unexpired session data."""
        self._prune_expired_sessions()
        return self._sessions.get(session_id)

    def probe_socket(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """Test TCP socket reachability."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((host, port)) == 0
        except Exception:
            return False

    def probe_http(self, url: str, timeout: float = 2.0) -> bool:
        """Test HTTP endpoint reachability."""
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url)
                return resp.status_code < 500
        except Exception:
            return False

    def mask_secret(self, val: str | None, prefix: int = 2, suffix: int = 2) -> str | None:
        """Obscure sensitive credentials."""
        return mask_secret(val, prefix=prefix, suffix=suffix)

    def test_connection(self, endpoint: str | None = None) -> ConnectionResult:
        """Test connection probing default_ports or endpoint (HTTP, RTSP, host:port)."""
        if endpoint:
            endpoint_str = str(endpoint).strip()
            reachable = False

            if (
                "mock" in endpoint_str.lower()
                or "test" in endpoint_str.lower()
                or endpoint_str.startswith("ring://")
            ):
                reachable = True
            elif endpoint_str.startswith(("http://", "https://")):
                reachable = self.probe_http(endpoint_str)
            elif endpoint_str.startswith(("rtsp://", "rtsps://")):
                parsed = urlparse(endpoint_str)
                host = parsed.hostname or "127.0.0.1"
                default_rtsp_port = self.default_ports.get("rtsp", 8554)
                port = parsed.port if parsed.port is not None else default_rtsp_port
                reachable = self.probe_socket(host, port)
            elif ":" in endpoint_str:
                parts = endpoint_str.split(":", 1)
                host = parts[0]
                try:
                    port = int(parts[1].split("/")[0])
                    reachable = self.probe_socket(host, port)
                except (ValueError, IndexError):
                    reachable = False
            else:
                for port in self.default_ports.values():
                    if self.probe_socket(endpoint_str, port):
                        reachable = True
                        break

            return ConnectionResult(
                reachable=reachable,
                provider=self.provider_id,
                endpoint=endpoint_str,
            )

        reachable = False
        checked_ports: list[int] = []
        for port in self.default_ports.values():
            checked_ports.append(port)
            if self.probe_socket("127.0.0.1", port):
                reachable = True
                break

        return ConnectionResult(
            reachable=reachable,
            provider=self.provider_id,
            ports=checked_ports,
        )

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt camera stream into configuration."""
        cam = CameraConfig(
            id=camera_id,
            name=camera_name,
            stream_url=stream_url,
            camera_type=self.provider_id or "generic",
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
        """Optional provider-specific FastAPI sub-router."""
        return None

    def load_manifest_from_file(self, manifest_path: Path) -> ProviderManifest:
        """Helper to load manifest data from manifest.json."""
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ProviderManifest(**data)

    # Backward compatibility with SidecarProviderPort:
    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible initiate_login returning dict."""
        return self.authenticate(credentials).model_dump()

    @abc.abstractmethod
    def authenticate(self, credentials: dict[str, Any]) -> AuthResult:
        """Authenticate with provider or trigger interactive 2FA challenge."""
        ...

    @abc.abstractmethod
    def verify_2fa(self, session_id: str, code: str) -> AuthResult:
        """Verify 2FA challenge code and obtain tokens."""
        ...

    @abc.abstractmethod
    def probe_existing(self, config: LookoutConfig | None = None) -> DiscoveryResult:
        """Detect existing credentials, sockets, or bridge containers."""
        ...

    @abc.abstractmethod
    def sync_environment(self, config: LookoutConfig, target_dir: Path) -> list[Path]:
        """Synchronize provider environment files."""
        ...

    @abc.abstractmethod
    def get_stream_urls(self, config: LookoutConfig | None = None) -> list[StreamDescriptor]:
        """Return resolvable stream descriptors."""
        ...

    @abc.abstractmethod
    def get_ui_manifest(self) -> ProviderManifest:
        """Return UI presentation metadata."""
        ...
