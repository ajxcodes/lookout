"""Unit tests for starter template skeleton provider driver."""

from pathlib import Path

from lookout.config.schema import LookoutConfig
from lookout.providers.templates.skeleton.driver import SkeletonProviderDriver


def test_skeleton_template_driver_contract(tmp_path: Path) -> None:
    """TR-12: Verify skeleton template driver contract, manifest, and synchronization."""
    driver = SkeletonProviderDriver()
    manifest = driver.get_ui_manifest()
    auth_res = driver.authenticate({"username": "admin", "password": "password"})
    assert manifest.id == "skeleton" and auth_res.status == "authenticated"

    assert manifest.default_ports.get("rtsp") == 8554

    verify_res = driver.verify_2fa("sess_dummy", "123456")
    assert verify_res.status == "authenticated"

    probe_res = driver.probe_existing()
    assert probe_res.provider == "skeleton"

    config = LookoutConfig()
    synced = driver.sync_environment(config, tmp_path)
    assert len(synced) == 1
    assert synced[0].is_file()
