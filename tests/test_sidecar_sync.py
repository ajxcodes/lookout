"""Unit tests for sidecar environment configuration synchronizer."""

from pathlib import Path

from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer
from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig


def test_sync_wyze_env_file_generation(tmp_path: Path) -> None:
    """TR-4: Verify Wyze sidecar environment file is generated with safe permissions."""
    config = LookoutConfig(
        system_name="Test Sync",
        wyze=WyzeConfig(
            email="user@example.com",
            password="test-password",
            auth_token="jwt-access-token",
        ),
    )
    syncer = SidecarConfigSynchronizer(base_dir=tmp_path)
    env_file = syncer.sync_provider("wyze", config)

    assert env_file.is_file() and "WYZE_EMAIL=user@example.com" in env_file.read_text()
    assert "WYZE_ACCESS_TOKEN=jwt-access-token" in env_file.read_text()
    assert "WB_IP=0.0.0.0" in env_file.read_text()
    assert (env_file.stat().st_mode & 0o777) == 0o600


def test_sync_ring_env_file_generation(tmp_path: Path) -> None:
    """TR-5: Verify Ring sidecar environment file is generated with safe permissions."""
    config = LookoutConfig(
        system_name="Test Sync",
        ring=RingConfig(
            email="ring-user@example.com",
            refresh_token="mock-ring-token",
        ),
    )
    syncer = SidecarConfigSynchronizer(base_dir=tmp_path)
    env_file = syncer.sync_provider("ring", config)

    assert env_file.is_file() and "RINGTOKEN=mock-ring-token" in env_file.read_text()
    assert "MQTTHOST=localhost" in env_file.read_text()
    assert (env_file.stat().st_mode & 0o777) == 0o600


def test_sync_all_providers(tmp_path: Path) -> None:
    """Verify sync_all synchronizes all registered providers."""
    config = LookoutConfig(
        system_name="Sync All",
        wyze=WyzeConfig(email="all@wyze.com"),
        ring=RingConfig(refresh_token="all-ring-token"),
    )
    syncer = SidecarConfigSynchronizer(base_dir=tmp_path)
    paths = syncer.sync_all(config)

    assert "wyze" in paths and "ring" in paths
    assert paths["wyze"].exists() and paths["ring"].exists()


def test_sync_wyze_env_with_api_key_and_id(tmp_path: Path) -> None:
    """Verify Wyze sidecar environment file includes API_KEY and API_ID when configured."""
    config = LookoutConfig(
        system_name="Wyze Keys Test",
        wyze=WyzeConfig(
            email="user@example.com",
            api_key="my-api-key-123",
            key_id="my-key-id-456",
        ),
    )
    syncer = SidecarConfigSynchronizer(base_dir=tmp_path)
    env_file = syncer.sync_provider("wyze", config)
    content = env_file.read_text()

    assert "API_KEY=my-api-key-123" in content
    assert "WYZE_API_KEY=my-api-key-123" in content
    assert "API_ID=my-key-id-456" in content
    assert "WYZE_API_ID=my-key-id-456" in content
