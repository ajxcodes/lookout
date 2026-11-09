"""Concrete sidecar adapters for Wyze and Ring wrapping modular provider drivers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lookout.config.schema import LookoutConfig
from lookout.providers.ring.driver import RingProviderDriver
from lookout.providers.wyze.driver import WyzeProviderDriver

logger = logging.getLogger(__name__)


class WyzeSidecarAdapter(WyzeProviderDriver):
    """Adapter for docker-wyze-bridge subclassing WyzeProviderDriver for backward compatibility."""

    def mock_login(
        self,
        email: str,
        password: str,
        api_key: str | None = None,
        api_id: str | None = None,
    ) -> dict[str, Any]:
        """Simulate Wyze 2FA challenge initiation."""
        return self.authenticate(
            {"email": email, "password": password, "api_key": api_key, "api_id": api_id}
        ).model_dump()

    def mock_verify(self, session_id: str, code: str) -> dict[str, Any]:
        """Simulate Wyze 2FA verification and token issuance."""
        return self.verify_2fa(session_id, code).model_dump()

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:  # type: ignore[override]
        """Synchronize Wyze bridge environment file with 0o600 permissions."""
        paths = super().sync_environment(config, base_dir)
        return paths[0]


class RingSidecarAdapter(RingProviderDriver):
    """Adapter for ring-mqtt subclassing RingProviderDriver for backward compatibility."""

    def mock_login(self, email: str, password: str) -> dict[str, Any]:
        """Simulate Ring 2FA challenge initiation."""
        return self.authenticate({"email": email, "password": password}).model_dump()

    def mock_verify(self, session_id: str, code: str) -> dict[str, Any]:
        """Simulate Ring 2FA verification returning RINGTOKEN."""
        return self.verify_2fa(session_id, code).model_dump()

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:  # type: ignore[override]
        """Synchronize ring-mqtt environment file with 0o600 permissions."""
        paths = super().sync_environment(config, base_dir)
        return paths[0]
