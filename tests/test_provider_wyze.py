"""Unit tests for self-contained Wyze provider driver."""

from pathlib import Path

from lookout.config.schema import LookoutConfig, WyzeConfig
from lookout.providers.wyze.driver import WyzeProviderDriver


def test_wyze_driver_auth_and_2fa_verification() -> None:
    """TR-8: Verify Wyze driver authentication challenge and 2FA verification."""
    driver = WyzeProviderDriver()
    auth_res = driver.authenticate({"email": "user@wyze.com", "password": "secure-password"})
    assert auth_res.status == "2fa_required"
    assert auth_res.session_id is not None

    verify_res = driver.verify_2fa(auth_res.session_id, "123456")
    assert verify_res.status == "authenticated" and verify_res.auth_token == "mock-wyze-token-12345"
    assert verify_res.email == "user@wyze.com"


def test_wyze_driver_environment_sync(tmp_path: Path) -> None:
    """TR-9: Verify Wyze sidecar environment file generation."""
    driver = WyzeProviderDriver()
    config = LookoutConfig(
        wyze=WyzeConfig(
            email="user@wyze.com",
            password="test-password",
            auth_token="token-123",
            api_key="key-abc",
            key_id="id-xyz",
        )
    )
    synced = driver.sync_environment(config, tmp_path)
    assert len(synced) == 1
    env_path = synced[0]
    assert env_path.is_file() and "WYZE_EMAIL=user@wyze.com" in env_path.read_text()
    assert "WYZE_ACCESS_TOKEN=token-123" in env_path.read_text()
    assert (env_path.stat().st_mode & 0o777) == 0o600
