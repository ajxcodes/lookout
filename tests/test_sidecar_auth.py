"""Unit tests for Wyze and Ring sidecar authentication adapters, 2FA, and secret masking."""

from lookout.adapters.sidecar_auth import RingSidecarAdapter, WyzeSidecarAdapter
from lookout.config.schema import mask_config_dict, mask_secret


def test_wyze_2fa_challenge_and_verification() -> None:
    """TR-1: Verify Wyze adapter initiates 2FA and verifies challenge code."""
    adapter = WyzeSidecarAdapter()
    login_res = adapter.initiate_login({"email": "user@example.com", "password": "secure-password"})
    assert login_res["status"] == "2fa_required"
    assert "session_id" in login_res

    result = adapter.verify_2fa(login_res["session_id"], "123456")
    assert result["status"] == "authenticated" and "auth_token" in result
    assert result["email"] == "user@example.com"


def test_ring_2fa_challenge_and_verification() -> None:
    """TR-2: Verify Ring adapter initiates 2FA and returns RINGTOKEN upon verification."""
    adapter = RingSidecarAdapter()
    login_res = adapter.initiate_login({"email": "ring@example.com", "password": "secure-password"})
    assert login_res["status"] == "2fa_required"
    assert "session_id" in login_res

    result = adapter.verify_2fa(login_res["session_id"], "654321")
    assert result["status"] == "authenticated" and result["refresh_token"] == "mock-ringtoken-12345"
    assert result["auth_token"] == "mock-ringtoken-12345"


def test_secret_masking_in_config_and_audit() -> None:
    """TR-3: Verify secret masking engine obscures passwords and tokens."""
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

    # Direct mask_secret tests
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
    """Verify probe_existing detects remote configured bridges."""
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
