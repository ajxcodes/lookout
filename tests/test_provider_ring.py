"""Unit tests for self-contained Ring provider driver."""

from pathlib import Path

from lookout.config.schema import LookoutConfig, RingConfig
from lookout.providers.ring.driver import RingProviderDriver


def test_ring_driver_auth_and_2fa_verification() -> None:
    """TR-10: Verify Ring driver authentication challenge and 2FA verification."""
    driver = RingProviderDriver()
    auth_res = driver.authenticate({"email": "user@ring.com", "password": "secure-password"})
    assert auth_res.status == "2fa_required"
    assert auth_res.session_id is not None
    assert auth_res.phone == "***-***-1234"

    verify_res = driver.verify_2fa(auth_res.session_id, "654321")
    assert (
        verify_res.status == "authenticated" and verify_res.refresh_token == "mock-ringtoken-12345"
    )
    assert verify_res.email == "user@ring.com"


def test_ring_driver_environment_sync(tmp_path: Path) -> None:
    """TR-11: Verify Ring sidecar environment file generation."""
    driver = RingProviderDriver()
    config = LookoutConfig(
        ring=RingConfig(
            email="user@ring.com",
            refresh_token="mock-ring-token",
        )
    )
    synced = driver.sync_environment(config, tmp_path)
    assert len(synced) == 1
    env_path = synced[0]
    assert env_path.is_file() and "RINGTOKEN=mock-ring-token" in env_path.read_text()
    assert "MQTTHOST=localhost" in env_path.read_text()
    assert (env_path.stat().st_mode & 0o777) == 0o600
