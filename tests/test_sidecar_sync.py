"""Tests for sidecar config synchronization (now a no-op for eliminated bridges)."""

from __future__ import annotations

from pathlib import Path

from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer
from lookout.config.loader import load_config_from_yaml
from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig


def test_sync_wyze_env_file_generation(tmp_path: Path) -> None:
    cfg = LookoutConfig(wyze=WyzeConfig(email="a@b.com", password="secret"))
    sync = SidecarConfigSynchronizer(tmp_path)
    files = sync.sync_wyze_env(cfg)
    assert len(files) == 1
    assert (tmp_path / "wyze-bridge.env").exists()


def test_sync_ring_env_file_generation(tmp_path: Path) -> None:
    cfg = LookoutConfig(ring=RingConfig(refresh_token="rt-123"))
    sync = SidecarConfigSynchronizer(tmp_path)
    files = sync.sync_ring_env(cfg)
    assert len(files) == 1
    assert (tmp_path / "ring-mqtt.env").exists()


def test_sync_wyze_env_with_api_key_and_id(tmp_path: Path) -> None:
    """Legacy API-key/ID-only configs no longer produce a sidecar env file."""
    cfg = load_config_from_yaml(
        """
        wyze:
          key_id: "kid"
          api_key: "akey"
          totp_key: "JBSWY3DPEHPK3PXP"
        """
    )
    sync = SidecarConfigSynchronizer(tmp_path)
    files = sync.sync_wyze_env(cfg)
    assert files == []
    assert not (tmp_path / "wyze-bridge.env").exists()


def test_sync_wyze_env_without_tokens_is_skipped(tmp_path: Path) -> None:
    """TR-25 (AC7): no credentials means no wyze-bridge.env is written."""
    cfg = LookoutConfig(wyze=WyzeConfig())
    sync = SidecarConfigSynchronizer(tmp_path)
    files = sync.sync_wyze_env(cfg)
    assert files == []
    assert not (tmp_path / "wyze-bridge.env").exists()
