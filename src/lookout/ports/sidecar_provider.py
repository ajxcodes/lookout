from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lookout.config.schema import CameraConfig, LookoutConfig


@runtime_checkable
class SidecarProviderPort(Protocol):
    """Protocol for bridge sidecar authentication, probing, and config synchronization."""

    provider_id: str
    display_name: str
    default_ports: dict[str, int]

    def probe_existing(self, config: LookoutConfig | None = None) -> dict[str, Any]:
        """Detect existing environment variables, active sockets, or pre-authenticated bridges."""
        ...

    def initiate_login(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Initiate provider authentication or trigger interactive 2FA challenge."""
        ...

    def verify_2fa(self, session_id: str, code: str) -> dict[str, Any]:
        """Verify 2FA challenge code and obtain authenticated tokens."""
        ...

    def sync_environment(self, config: LookoutConfig, base_dir: Path) -> Path:
        """Synchronize provider container environment variables to env file."""
        ...

    def test_connection(self, endpoint: str | None = None) -> dict[str, Any]:
        """Verify stream or sidecar API endpoint reachability."""
        ...

    def adopt_stream(
        self, stream_url: str, camera_id: str, camera_name: str, config: LookoutConfig
    ) -> CameraConfig:
        """Adopt pre-existing stream into system camera configuration."""
        ...
