"""Presentation tests for the motion-tag toggle in the camera settings modal."""

from pathlib import Path

from lookout.adapters.web.helpers import build_camera_card_data
from lookout.config.schema import CameraConfig

INDEX = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_camera_settings_modal_exposes_motion_tag_toggle() -> None:
    index_html = INDEX.read_text(encoding="utf-8")
    app_js = APP.read_text(encoding="utf-8")
    assert 'id="cs-cam-motion-tag"' in index_html
    assert "loadMotionTagSupport" in app_js
    assert "/motion_tag" in app_js


def test_camera_card_exposes_motion_tag_state() -> None:
    cam = CameraConfig(
        id="cam1",
        name="Cam 1",
        stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&model=WYZE_CA&subtype=hd",
        native_provider="wyze",
        native_device_id="AA:BB:CC:DD:EE:FF",
        motion_tag_enabled=True,
    )
    card = build_camera_card_data(cam)
    assert card["motion_tag_enabled"] is True


def test_motion_tag_toggle_stays_hidden_for_unsupported_lane() -> None:
    """TR-29: the group is hidden by default and only revealed after support."""
    app_js = APP.read_text(encoding="utf-8")
    load_body = app_js.split("async loadMotionTagSupport(cam) {", 1)[1].split(
        "\n  async saveCameraSettings", 1
    )[0]

    assert 'this.csMotionTagGroup.style.display = "none"' in load_body
    guard = "if (!data || !data.supported)"
    assert guard in load_body
    assert load_body.index(guard) < load_body.index('this.csMotionTagGroup.style.display = ""')
