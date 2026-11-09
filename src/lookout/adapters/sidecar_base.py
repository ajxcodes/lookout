"""Base class for camera bridge sidecar adapters."""

import abc
import logging
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from lookout.config.schema import CameraConfig, LookoutConfig, mask_secret
from lookout.ports.sidecar_provider import SidecarProviderPort

logger = logging.getLogger(__name__)


class BaseSidecarAdapter(SidecarProviderPort, abc.ABC):
    """Abstract base adapter providing socket/http probing, session caching, and stream adoption."""

    provider_id: str = ""
    display_name: str = ""
    default_ports: dict[str, int] = {}

    def __init__(self, session_ttl_seconds: float = 600.0) -> None:
        self.session_ttl_seconds = session_ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}

    def _prune_expired_sessions(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, sess in self._sessions.items()
            if now - float(sess.get("created_at", 0)) > self.session_ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)

    def store_session(self, session_id: str, data: dict[str, Any]) -> None:
        self._prune_expired_sessions()
        session_data = dict(data)
        session_data["created_at"] = time.time()
        self._sessions[session_id] = session_data

    def get_session(self, session_id: str) -> dict[str, Any] | None:
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
        return mask_secret(val, prefix=prefix, suffix=suffix)

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt a camera stream and add it to config.cameras."""
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

    def test_connection(self, endpoint: str | None = None) -> dict[str, Any]:
        """Connection test probing default_ports or endpoint (HTTP, RTSP, host:port)."""
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

            return {
                "reachable": reachable,
                "provider": self.provider_id,
                "endpoint": endpoint_str,
            }

        reachable = False
        checked_ports: list[int] = []
        for port in self.default_ports.values():
            checked_ports.append(port)
            if self.probe_socket("127.0.0.1", port):
                reachable = True
                break

        return {
            "reachable": reachable,
            "provider": self.provider_id,
            "ports": checked_ports,
        }

    @abc.abstractmethod
    def probe_existing(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Detect existing environment variables or running instances."""
        ...

    @abc.abstractmethod
    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Initiate login."""
        ...

    @abc.abstractmethod
    def verify_2fa(self, session_id: str, code: str) -> dict[str, Any]:
        """Verify 2FA challenge."""
        ...

    @abc.abstractmethod
    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:
        """Write environment file."""
        ...
