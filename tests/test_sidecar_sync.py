"""Tests for sidecar config synchronization (now a no-op for eliminated bridges)."""

from __future__ import annotations

from pathlib import Path

from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer
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
    cfg = LookoutConfig(wyze=WyzeConfig(key_id="kid", api_key="akey"))
    sync = SidecarConfigSynchronizer(tmp_path)
    files = sync.sync_wyze_env(cfg)
    assert len(files) == 1
