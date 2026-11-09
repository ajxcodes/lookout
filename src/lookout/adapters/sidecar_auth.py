"""Concrete sidecar adapters for Wyze and Ring authentication, 2FA, and environment syncing."""

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from lookout.adapters.sidecar_base import BaseSidecarAdapter
from lookout.config.schema import CameraConfig, LookoutConfig

logger = logging.getLogger(__name__)


class WyzeSidecarAdapter(BaseSidecarAdapter):
    """Adapter for docker-wyze-bridge and Wyze cloud authentication."""

    provider_id: str = "wyze"
    display_name: str = "Wyze Cam Bridge"
    default_ports: dict[str, int] = {"http": 5000, "rtsp": 8554}

    def probe_existing(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Detect existing Wyze bridge, container environment variables, or config tokens."""
        env_email = os.environ.get("WYZE_EMAIL")
        env_token = os.environ.get("WYZE_ACCESS_TOKEN")
        env_api_key = os.environ.get("WYZE_API_KEY") or os.environ.get("API_KEY")
        has_config = (
            config is not None
            and config.wyze is not None
            and bool(
                config.wyze.email
                and (config.wyze.auth_token or config.wyze.password or config.wyze.api_key)
            )
        )
        is_port_open = self.probe_socket("127.0.0.1", 5000) or self.probe_socket("127.0.0.1", 8554)

        remote_reachable = False
        if config is not None and config.wyze is not None:
            if config.wyze.external_bridge_url:
                res = self.test_connection(config.wyze.external_bridge_url)
                remote_reachable = res["reachable"]
            if not remote_reachable and config.wyze.bridge_rtsp_url:
                res = self.test_connection(config.wyze.bridge_rtsp_url)
                remote_reachable = res["reachable"]

        if env_email or env_token or env_api_key or has_config or is_port_open or remote_reachable:
            active_email = (
                env_email
                or (config.wyze.email if config and config.wyze else None)
                or "wyze_user@local"
            )
            return {
                "status": "pre_authenticated",
                "provider": self.provider_id,
                "mode": "pre_authenticated",
                "details": "Pre-existing Wyze credentials or running bridge detected",
                "email": active_email,
                "bridge_reachable": is_port_open or remote_reachable,
            }

        return {
            "status": "disconnected",
            "provider": self.provider_id,
            "mode": "unconfigured",
            "details": "No existing Wyze bridge or credentials detected",
        }

    def mock_login(
        self,
        email: str,
        password: str,
        api_key: str | None = None,
        api_id: str | None = None,
    ) -> dict[str, Any]:
        """Simulate Wyze 2FA challenge initiation."""
        session_id = f"wyze_session_{uuid.uuid4().hex[:12]}"
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
        return {
            "status": "2fa_required",
            "session_id": session_id,
            "provider": self.provider_id,
            "method": "email/sms/totp",
            "message": "2FA verification code sent to your registered Wyze device/email",
        }

    def mock_verify(self, session_id: str, code: str) -> dict[str, Any]:
        """Simulate Wyze 2FA verification and token issuance."""
        session = self.get_session(session_id)
        if not session:
            return {"status": "error", "message": "Invalid or expired session"}

        email = session.get("email", "")
        return {
            "status": "authenticated",
            "provider": self.provider_id,
            "auth_token": "mock-wyze-token-12345",
            "refresh_token": "mock-wyze-refresh-token-12345",
            "email": email,
        }

    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Initiate Wyze authentication flow."""
        email = credentials.get("email", "")
        password = credentials.get("password", "")
        api_key = credentials.get("api_key")
        api_id = credentials.get("api_id")
        return self.mock_login(email=email, password=password, api_key=api_key, api_id=api_id)

    def verify_2fa(self, session_id: str, code: str) -> dict[str, Any]:
        """Verify 2FA challenge code."""
        return self.mock_verify(session_id=session_id, code=code)

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:
        """Synchronize Wyze bridge environment file with 0o600 permissions."""
        base_dir.mkdir(parents=True, exist_ok=True)
        env_file = base_dir / "wyze-bridge.env"

        email = (config.wyze.email if config.wyze else None) or os.environ.get("WYZE_EMAIL") or ""
        password = (
            (config.wyze.password if config.wyze else None) or os.environ.get("WYZE_PASSWORD") or ""
        )
        token = (
            (config.wyze.auth_token if config.wyze else None)
            or os.environ.get("WYZE_ACCESS_TOKEN")
            or ""
        )
        api_key = (
            (config.wyze.api_key if config.wyze else None)
            or os.environ.get("WYZE_API_KEY")
            or os.environ.get("API_KEY")
            or ""
        )
        key_id = (
            (config.wyze.key_id if config.wyze else None)
            or os.environ.get("WYZE_KEY_ID")
            or os.environ.get("WYZE_API_ID")
            or os.environ.get("API_ID")
            or ""
        )

        lines = [
            f"WYZE_EMAIL={email}",
            f"WYZE_PASSWORD={password}",
            f"WYZE_ACCESS_TOKEN={token}",
            "WB_IP=0.0.0.0",
            "ON_DEMAND=True",
        ]
        if api_key:
            lines.append(f"API_KEY={api_key}")
            lines.append(f"WYZE_API_KEY={api_key}")
        if key_id:
            lines.append(f"API_ID={key_id}")
            lines.append(f"WYZE_API_ID={key_id}")

        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env_file.chmod(0o600)
        return env_file

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt Wyze stream as wyze camera type."""
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


class RingSidecarAdapter(BaseSidecarAdapter):
    """Adapter for ring-mqtt and Ring OAuth authentication."""

    provider_id: str = "ring"
    display_name: str = "Ring Video Doorbell"
    default_ports: dict[str, int] = {"http": 55123, "rtsp": 8555}

    def probe_existing(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Detect existing Ring token in environment, config, or running ring-mqtt instance."""
        env_token = os.environ.get("RING_TOKEN") or os.environ.get("RINGTOKEN")
        has_config_token = (
            config is not None
            and config.ring is not None
            and bool(config.ring.refresh_token or config.ring.auth_token)
        )
        is_port_open = (
            self.probe_socket("127.0.0.1", 55123)
            or self.probe_socket("127.0.0.1", 8555)
            or self.probe_socket("127.0.0.1", 8554)
        )

        remote_reachable = False
        if config is not None and config.ring is not None and config.ring.external_stream_url:
            res = self.test_connection(config.ring.external_stream_url)
            remote_reachable = res["reachable"]

        if env_token or has_config_token or is_port_open or remote_reachable:
            active_email = (
                os.environ.get("RING_EMAIL")
                or (config.ring.email if config and config.ring else None)
                or "ring_user@local"
            )
            return {
                "status": "pre_authenticated",
                "provider": self.provider_id,
                "mode": "pre_authenticated",
                "details": "Pre-existing Ring token or ring-mqtt bridge detected",
                "token_present": True,
                "email": active_email,
                "bridge_reachable": is_port_open or remote_reachable,
            }

        return {
            "status": "disconnected",
            "provider": self.provider_id,
            "mode": "unconfigured",
            "details": "No existing Ring bridge or credentials detected",
        }

    def mock_login(self, email: str, password: str) -> dict[str, Any]:
        """Simulate Ring 2FA challenge initiation."""
        session_id = f"ring_session_{uuid.uuid4().hex[:12]}"
        self.store_session(
            session_id,
            {
                "email": email,
                "password": password,
                "provider": self.provider_id,
            },
        )
        return {
            "status": "2fa_required",
            "session_id": session_id,
            "provider": self.provider_id,
            "phone": "***-***-1234",
            "message": "2FA code sent via SMS to your registered phone",
        }

    def mock_verify(self, session_id: str, code: str) -> dict[str, Any]:
        """Simulate Ring 2FA verification returning RINGTOKEN."""
        session = self.get_session(session_id)
        if not session:
            return {"status": "error", "message": "Invalid or expired session"}

        email = session.get("email", "")
        return {
            "status": "authenticated",
            "provider": self.provider_id,
            "refresh_token": "mock-ringtoken-12345",
            "auth_token": "mock-ringtoken-12345",
            "email": email,
        }

    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Initiate Ring authentication flow."""
        email = credentials.get("email", "")
        password = credentials.get("password", "")
        return self.mock_login(email=email, password=password)

    def verify_2fa(self, session_id: str, code: str) -> dict[str, Any]:
        """Verify Ring 2FA code."""
        return self.mock_verify(session_id=session_id, code=code)

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:
        """Synchronize ring-mqtt environment file with 0o600 permissions."""
        base_dir.mkdir(parents=True, exist_ok=True)
        env_file = base_dir / "ring-mqtt.env"

        token = (
            (config.ring.refresh_token if config.ring else None)
            or (config.ring.auth_token if config.ring else None)
            or os.environ.get("RING_TOKEN")
            or os.environ.get("RINGTOKEN")
            or ""
        )

        lines = [
            f"RINGTOKEN={token}",
            "MQTTHOST=localhost",
            "MQTTPORT=1883",
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env_file.chmod(0o600)
        return env_file

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt Ring stream as ring camera type."""
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
