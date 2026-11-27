"""Presentation tests for the Pan Scan toggle in the camera settings modal."""

from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_camera_settings_modal_exposes_pan_scan_toggle() -> None:
    index_html = INDEX.read_text(encoding="utf-8")
    app_js = APP.read_text(encoding="utf-8")
    assert 'id="cs-cam-pan-scan"' in index_html
    assert "loadPanScanSupport" in app_js
    assert "/pan_scan" in app_js


def test_pan_scan_toggle_stays_hidden_for_unsupported_camera() -> None:
    """TR-18: the group is hidden by default and only revealed after support."""
    app_js = APP.read_text(encoding="utf-8")
    load_body = app_js.split("async loadPanScanSupport(cam) {", 1)[1].split(
        "\n  async saveCameraSettings", 1
    )[0]

    assert 'this.csPanScanGroup.style.display = "none"' in load_body
    guard = "if (!data || !data.supported)"
    assert guard in load_body
    assert load_body.index(guard) < load_body.index('this.csPanScanGroup.style.display = ""')


def test_pan_scan_ui_warns_when_smart_tracking_is_on() -> None:
    """TR-19: a live smart-tracking state surfaces the pause warning."""
    app_js = APP.read_text(encoding="utf-8")
    assert "data.tracking === true" in app_js
    assert '"Smart tracking is on; Pan Scan may be paused."' in app_js
