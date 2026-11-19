"""AC10 - explicit, universal camera removal; logout never removes cameras."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from auth_test_helpers import inject_fake_issuers, make_client

from lookout.adapters.ring_native import encode_ring_token
from lookout.config.schema import CameraConfig, LookoutConfig, RingConfig, StorageConfig
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel

_TOKEN = encode_ring_token({"rt": "refresh", "hid": "hid", "pnc": {"token": "push"}})
_JS_DIR = Path(__file__).parents[1] / "src/lookout/adapters/web/static/js"


def _config(tmp_path: Path) -> LookoutConfig:
    return LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        ring=RingConfig(refresh_token=_TOKEN, auth_token=_TOKEN, native_device_ids=["ring-dev-1"]),
        cameras=[
            CameraConfig(
                id="native-ring",
                name="Front Door",
                stream_url="ring:?camera_id=1",
                native_provider="ring",
                native_device_id="ring-dev-1",
            ),
            CameraConfig(
                id="rtsp-cam",
                name="Garage",
                stream_url="rtsp://example/cam",
                camera_type="generic",
            ),
        ],
    )


def _render_tiles_via_live_grid(tmp_path: Path) -> dict:
    """Render real camera tiles with LiveGrid.setCameras() in a headless DOM.

    The management controls must be attached to the *rendered* tiles, not merely
    present as strings in app.js (the previous false-positive assertion).
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the LiveGrid DOM-level test")
    harness = Path(__file__).parent / "js" / "camera_management_dom_check.mjs"
    proc = subprocess.run(
        [node, str(harness), str(_JS_DIR)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    for line in proc.stdout.splitlines():
        if line.startswith("DOM_RESULT="):
            return json.loads(line[len("DOM_RESULT=") :])
    raise AssertionError(f"DOM harness produced no result: {proc.stdout} {proc.stderr}")


def test_every_camera_card_exposes_remove_button(tmp_path: Path) -> None:
    """TR-21 (AC10): Remove control is universal; auth badge is informational.

    Strengthened to a DOM-level test: tiles are rendered via LiveGrid.setCameras()
    and the controls are asserted on the actual tile elements.
    """
    wizard_js = (_JS_DIR / "setup-wizard.js").read_text(encoding="utf-8")
    assert "logout" in wizard_js.lower()

    # DOM-level assertion on the real rendered tiles.
    rendered = _render_tiles_via_live_grid(tmp_path)
    assert rendered["tileCount"] == 4
    tiles = {t["cameraId"]: t for t in rendered["tiles"]}
    assert set(tiles) == {"native-ring", "native-wyze", "rtsp", "go2"}
    for tile in tiles.values():
        assert tile["hasBar"] is True, f"missing management bar on {tile['cameraId']}"
        assert "Remove camera" in tile["buttons"], tile

    # Unauthorized native camera: informational badge + Reauthenticate suggestion.
    ring = tiles["native-ring"]
    assert ring["hasBadge"] is True
    assert ring["badgeText"] == "Authentication required"
    assert "Reauthenticate" in ring["buttons"]

    # Every other camera: Remove is still available, but no auth gating.
    for cam_id in ("native-wyze", "rtsp", "go2"):
        assert tiles[cam_id]["hasBadge"] is False
        assert "Reauthenticate" not in tiles[cam_id]["buttons"]

    # The wired handlers actually fire (controls are interactive, not decorative).
    assert "native-ring" in rendered["callsAfterClick"]["reauthenticate"]
    assert "native-ring" in rendered["callsAfterClick"]["remove"]

    config = LookoutConfig(
        ring=RingConfig(refresh_token=_TOKEN, logged_out=True),
        cameras=[
            CameraConfig(
                id="native-ring",
                name="Door",
                stream_url="ring:?camera_id=1",
                native_provider="ring",
            ),
            CameraConfig(
                id="native-wyze",
                name="Office",
                stream_url="wyze://10.0.0.2?uid=u",
                native_provider="wyze",
            ),
            CameraConfig(id="rtsp", name="RTSP", stream_url="rtsp://example/cam"),
            CameraConfig(
                id="go2", name="Go2", stream_url="rtsp://example/go2", camera_type="go2rtc"
            ),
        ],
    )
    client, _app, _path = make_client(Path("/tmp/opencode/lookout-cam-cards"), config)
    cards = {card["id"]: card for card in client.get("/api/cameras").json()["cameras"]}
    assert set(cards) == {"native-ring", "native-wyze", "rtsp", "go2"}
    assert cards["native-ring"]["auth_required"] is True
    assert cards["rtsp"]["auth_required"] is False
    assert cards["go2"]["auth_required"] is False


async def test_remove_camera_deletes_config_and_streams_but_keeps_events(
    tmp_path: Path,
) -> None:
    """TR-22 (AC10): DELETE removes config/streams but preserves history."""
    config = _config(tmp_path)
    client, app, _path = make_client(tmp_path, config)
    manager = app.state.native_stream_manager
    manager.streams["native-ring"] = "ring:?camera_id=1"

    event = CameraEvent(
        id="historic-event",
        camera_id="native-ring",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.INFO,
        summary="Historic motion",
        media_uri="events/historic.mp4",
    )
    await app.state.repository.save(event)
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "historic.mp4").write_bytes(b"historical-footage")

    native_response = client.delete("/api/cameras/native-ring")
    assert native_response.status_code == 200
    assert native_response.json()["status"] == "removed"
    assert all(c.id != "native-ring" for c in config.cameras)
    assert "ring-dev-1" not in config.ring.native_device_ids
    assert "native-ring" not in manager.streams

    generic_response = client.delete("/api/v1/cameras/rtsp-cam")
    assert generic_response.status_code == 200
    assert all(c.id != "rtsp-cam" for c in config.cameras)

    events = client.get("/api/events").json()
    assert any(e.get("id") == "historic-event" for e in events.get("events", []))
    assert client.get("/media/historic.mp4").status_code == 200


async def test_logout_and_poller_error_never_auto_remove_camera(tmp_path: Path) -> None:
    """TR-23 (AC9/AC10): logout or a failing poll never removes a camera."""
    inject_fake_issuers()
    config = _config(tmp_path)
    client, app, _path = make_client(tmp_path, config)

    client.post("/api/auth/ring/logout", json={})
    assert any(c.id == "native-ring" for c in config.cameras)

    async def failing_poll(camera_id, since=None):
        raise RuntimeError("401 Unauthorized")

    app.state.ring_native.poll_events = failing_poll
    poller = getattr(client.app.state, "native_event_poller", None)
    if poller is None:
        from lookout.adapters.native_event_poller import NativeEventPoller

        async def noop(camera_id, payload):
            return {"id": "x"}

        poller = NativeEventPoller(
            config=config,
            wyze=app.state.wyze_native,
            ring=app.state.ring_native,
            ingest=noop,
        )
        client.app.state.native_event_poller = poller
    await poller._tick()
    assert poller.consecutive_failures >= 1
    assert any(c.id == "native-ring" for c in config.cameras)
