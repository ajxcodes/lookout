"""AC8/AC9 - explicit logout clears secrets, pauses ingest and preserves cameras."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path

from auth_test_helpers import FakeRingIssuer, inject_fake_issuers, make_client

from lookout.adapters.native_event_poller import NativeEventPoller
from lookout.adapters.ring_native import encode_ring_token
from lookout.config.schema import CameraConfig, LookoutConfig, RingConfig, WyzeConfig

_VALID_TOKEN = encode_ring_token({"rt": "real-refresh", "hid": "hid", "pnc": {"token": "push"}})


def _sidecar(tmp_path: Path) -> Path:
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    env_file = sidecar_dir / "ring-mqtt.env"
    env_file.write_text(f"RINGTOKEN={_VALID_TOKEN}\nMQTTHOST=localhost\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    return sidecar_dir


def test_logout_clears_config_and_sidecar_secrets(tmp_path: Path) -> None:
    """TR-17 (AC8): logout nulls secrets, redacts sidecar, returns a redacted result."""
    inject_fake_issuers()
    config = LookoutConfig(ring=RingConfig(refresh_token=_VALID_TOKEN, auth_token=_VALID_TOKEN))
    client, app, _path = make_client(tmp_path, config)
    app.state.sidecar_dir = _sidecar(tmp_path)

    response = client.post("/api/auth/ring/logout", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "logged_out"
    assert _VALID_TOKEN not in response.text
    assert config.ring.auth_token is None
    assert config.ring.refresh_token is None
    assert config.ring.logged_out is True

    env_file = Path(app.state.sidecar_dir) / "ring-mqtt.env"
    assert "RINGTOKEN=" not in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_logout_suppresses_env_and_sidecar_rediscovery(tmp_path: Path, monkeypatch) -> None:
    """TR-18 (AC8/AC3): logged_out forces reauth; verify clears the flag."""
    issuer = FakeRingIssuer()
    inject_fake_issuers(ring=issuer)
    monkeypatch.setenv("RINGTOKEN", _VALID_TOKEN)
    config = LookoutConfig(ring=RingConfig(refresh_token=_VALID_TOKEN, logged_out=True))
    client, app, _path = make_client(tmp_path, config)
    app.state.sidecar_dir = _sidecar(tmp_path)

    login = client.post(
        "/api/auth/ring/login", json={"email": "ring@example.com", "password": "pw"}
    )
    assert login.json()["status"] == "2fa_required"
    assert issuer.begin_calls == 1

    verify = client.post(
        "/api/auth/ring/verify",
        json={"session_id": login.json()["session_id"], "code": "654321"},
    )
    assert verify.json()["status"] == "authenticated"
    assert config.ring.logged_out is False

    again = client.post(
        "/api/auth/ring/login", json={"email": "ring@example.com", "password": "pw"}
    )
    assert again.json()["status"] == "pre_authenticated"
    assert issuer.begin_calls == 1


def test_logout_is_idempotent_and_pauses_poller(tmp_path: Path) -> None:
    """TR-19 (AC8): repeated logout succeeds and pauses the native poller."""
    inject_fake_issuers()
    config = LookoutConfig(ring=RingConfig(refresh_token=_VALID_TOKEN))
    client, app, _path = make_client(tmp_path, config)

    async def noop(camera_id, payload):
        return {"id": "x"}

    poller = NativeEventPoller(
        config=config,
        wyze=app.state.wyze_native,
        ring=app.state.ring_native,
        ingest=noop,
    )
    app.state.native_event_poller = poller

    first = client.post("/api/auth/ring/logout", json={})
    second = client.post("/api/auth/ring/logout", json={})
    assert first.json()["status"] == "logged_out"
    assert second.json()["status"] == "logged_out"
    assert "ring" in poller.health["paused_providers"]

    status = client.get("/api/native/status").json()
    assert status["logged_out"]["ring"] is True
    assert isinstance(status["auth_required_cameras"], list)


def test_logout_does_not_remove_or_mutate_cameras(tmp_path: Path) -> None:
    """TR-20 (AC9): config.cameras is byte-identical after logout for both providers."""
    inject_fake_issuers()
    config = LookoutConfig(
        wyze=WyzeConfig(auth_token="wyze-token", refresh_token="wyze-refresh"),
        ring=RingConfig(refresh_token=_VALID_TOKEN, auth_token=_VALID_TOKEN),
        cameras=[
            CameraConfig(
                id="native-ring",
                name="Front Door",
                stream_url="ring:?camera_id=1",
                native_provider="ring",
                native_device_id="ring-dev-1",
                notifications_muted=True,
            ),
            CameraConfig(
                id="native-wyze",
                name="Office",
                stream_url="wyze://10.0.0.2?uid=u",
                native_provider="wyze",
                native_device_id="wyze-dev-1",
                ptz_enabled=True,
                ptz_protocol="native-wyze",
            ),
            CameraConfig(id="rtsp", name="RTSP", stream_url="rtsp://example/cam"),
        ],
    )
    before = copy.deepcopy(config.cameras)
    client, _app, _path = make_client(tmp_path, config)

    client.post("/api/auth/ring/logout", json={})
    client.post("/api/auth/wyze/logout", json={})

    assert config.cameras == before
    assert config.ring.logged_out is True and config.wyze.logged_out is True
