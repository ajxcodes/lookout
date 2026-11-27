"""Presentation tests for the Wyze push-notification panel in camera settings."""

from pathlib import Path

from lookout.adapters.web.helpers import build_camera_card_data
from lookout.config.schema import CameraConfig

INDEX = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_camera_settings_modal_exposes_wyze_push_panel() -> None:
    index_html = INDEX.read_text(encoding="utf-8")
    app_js = APP.read_text(encoding="utf-8")
    assert 'id="cs-wyze-push-group"' in index_html
    assert 'data-push-key="MotionPush"' in index_html
    assert "/wyze_push_settings" in app_js


def test_wyze_push_group_stays_hidden_until_supported() -> None:
    """TR-25: the group is hidden by default and only revealed after support."""
    app_js = APP.read_text(encoding="utf-8")
    load_body = app_js.split("async loadPushSettingsSupport(cam) {", 1)[1].split(
        "\n  async saveCameraSettings", 1
    )[0]

    assert 'this.csWyzePushGroup.style.display = "none"' in load_body
    guard = "if (!data || !data.supported)"
    assert guard in load_body
    assert load_body.index(guard) < load_body.index('this.csWyzePushGroup.style.display = ""')


def test_wyze_push_ui_is_captioned_as_cloud_settings() -> None:
    index_html = INDEX.read_text(encoding="utf-8")
    assert "Wyze cloud push notifications" in index_html
    assert "separate from Lookout" in index_html


def test_camera_card_exposes_wyze_push_settings() -> None:
    cam = CameraConfig(
        id="cam1",
        name="Cam 1",
        stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_CAM4&subtype=hd",
        native_provider="wyze",
        wyze_push_settings={"MotionPush": True},
    )
    card = build_camera_card_data(cam)
    assert card["wyze_push_settings"] == {"MotionPush": True}
