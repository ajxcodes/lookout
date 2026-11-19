"""Unit tests for Wyze and Ring sidecar authentication adapters, 2FA, and secret masking."""

from pathlib import Path

from auth_test_helpers import (
    RING_ISSUED_TOKEN,
    WYZE_ISSUED_ACCESS,
    FakeRingIssuer,
    FakeWyzeIssuer,
)

from lookout.adapters.sidecar_auth import RingSidecarAdapter, WyzeSidecarAdapter
from lookout.config.schema import mask_config_dict, mask_secret


def test_wyze_2fa_challenge_and_verification() -> None:
    """Wyze adapter initiates 2FA and verifies via the injected issuer."""
    adapter = WyzeSidecarAdapter(token_issuer=FakeWyzeIssuer())
    login_res = adapter.initiate_login({"email": "user@example.com", "password": "secure-password"})
    assert login_res["status"] == "2fa_required"
    assert "session_id" in login_res

    result = adapter.verify_2fa(login_res["session_id"], "123456")
    assert result["status"] == "authenticated" and result["auth_token"] == WYZE_ISSUED_ACCESS
    assert result["email"] == "user@example.com"


def test_ring_2fa_challenge_and_verification() -> None:
    """Ring adapter initiates 2FA and returns the issuer-issued RINGTOKEN."""
    adapter = RingSidecarAdapter(token_issuer=FakeRingIssuer())
    login_res = adapter.initiate_login({"email": "ring@example.com", "password": "secure-password"})
    assert login_res["status"] == "2fa_required"
    assert "session_id" in login_res

    result = adapter.verify_2fa(login_res["session_id"], "654321")
    assert result["status"] == "authenticated" and result["refresh_token"] == RING_ISSUED_TOKEN
    assert result["auth_token"] == RING_ISSUED_TOKEN


def test_secret_masking_in_config_and_audit() -> None:
    """Secret masking engine obscures passwords and tokens."""
    raw_dict = {
        "email": "user@example.com",
        "password": "supersecretpassword",
        "auth_token": "token",
        "refresh_token": "jwt-refresh-secret-12345",
    }
    masked = mask_config_dict(raw_dict)
    assert masked["password"] == "******" and masked["auth_token"] == "to***en"
    assert masked["refresh_token"] == "jw***45"
    assert masked["email"] == "user@example.com"

    assert mask_secret(None) is None
    assert mask_secret("") == ""
    assert mask_secret("abc") == "******"
    assert mask_secret("abcdef") == "ab***ef"


def test_test_connection_rtsp_and_host_port() -> None:
    """Verify test_connection parses RTSP and host:port endpoints."""
    from unittest.mock import patch

    wyze = WyzeSidecarAdapter()
    ring = RingSidecarAdapter()

    assert ring.test_connection("ring://doorbell_1")["reachable"]

    with patch.object(wyze, "probe_socket", return_value=True):
        res_rtsp = wyze.test_connection("rtsp://192.0.2.1:8554/cam")
        assert res_rtsp["reachable"] is True
        assert res_rtsp["endpoint"] == "rtsp://192.0.2.1:8554/cam"
        res_hostport = wyze.test_connection("192.0.2.1:8554")
        assert res_hostport["reachable"] is True

    with patch.object(ring, "probe_socket", return_value=True):
        res_rtsp_ring = ring.test_connection("rtsp://192.0.2.1:8555/cam")
        assert res_rtsp_ring["reachable"] is True
        res_hostport_ring = ring.test_connection("192.0.2.1:8555")
        assert res_hostport_ring["reachable"] is True


def test_probe_existing_with_remote_endpoints() -> None:
    """probe_existing detects remote configured bridges."""
    from unittest.mock import patch

    from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig

    wyze = WyzeSidecarAdapter()
    ring = RingSidecarAdapter()

    config_wyze = LookoutConfig(
        system_name="Remote Wyze",
        wyze=WyzeConfig(bridge_rtsp_url="rtsp://192.0.2.1:8554/cam"),
    )
    with patch.object(wyze, "test_connection", return_value={"reachable": True}):
        status = wyze.probe_existing(config_wyze)
        assert status["status"] == "pre_authenticated"
        assert status["bridge_reachable"] is True

    config_ring = LookoutConfig(
        system_name="Remote Ring",
        ring=RingConfig(external_stream_url="rtsp://192.0.2.1:8555/cam"),
    )
    with patch.object(ring, "test_connection", return_value={"reachable": True}):
        status_ring = ring.probe_existing(config_ring)
        assert status_ring["status"] == "pre_authenticated"
        assert status_ring["bridge_reachable"] is True


def test_sidecar_adapters_backward_compatibility_and_driver_delegation(tmp_path: Path) -> None:
    """Backward-compatible mock_login/mock_verify delegate to real issuers."""
    from lookout.providers.wyze.driver import WyzeProviderDriver

    wyze_adapter = WyzeSidecarAdapter(token_issuer=FakeWyzeIssuer())
    assert isinstance(wyze_adapter, WyzeProviderDriver)
    assert wyze_adapter.provider_id == "wyze"

    ring_adapter = RingSidecarAdapter(token_issuer=FakeRingIssuer())
    assert ring_adapter.provider_id == "ring"

    wyze_login = wyze_adapter.mock_login(
        "user@example.com", "secure-password", api_key="k1", api_id="id1"
    )
    assert wyze_login["status"] == "2fa_required"
    assert "session_id" in wyze_login
    wyze_verify = wyze_adapter.mock_verify(wyze_login["session_id"], "123456")
    assert wyze_verify["status"] == "authenticated"
    assert wyze_verify["email"] == "user@example.com"

    ring_login = ring_adapter.mock_login("ring@example.com", "secure-password")
    assert ring_login["status"] == "2fa_required"
    assert "session_id" in ring_login
    ring_verify = ring_adapter.mock_verify(ring_login["session_id"], "654321")
    assert ring_verify["status"] == "authenticated"
    assert ring_verify["auth_token"] == RING_ISSUED_TOKEN

    from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig

    cfg = LookoutConfig(
        system_name="Test System",
        wyze=WyzeConfig(email="user@example.com", password="password"),
        ring=RingConfig(email="ring@example.com", refresh_token="token123"),
    )
    wyze_env = wyze_adapter.sync_environment(cfg, tmp_path)
    assert isinstance(wyze_env, Path)
    assert wyze_env.name == "wyze-bridge.env"
    assert wyze_env.exists()

    ring_env = ring_adapter.sync_environment(cfg, tmp_path)
    assert isinstance(ring_env, Path)
    assert ring_env.name == "ring-mqtt.env"
    assert ring_env.exists()
