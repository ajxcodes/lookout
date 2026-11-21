from pathlib import Path

LIVE_GRID = Path("src/lookout/adapters/web/static/js/components/live-grid.js")
CAM_MGMT = Path("src/lookout/adapters/web/static/js/components/camera-management.js")
INDEX = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_camera_tile_has_always_visible_gear_icon() -> None:
    src = CAM_MGMT.read_text(encoding="utf-8") + LIVE_GRID.read_text(encoding="utf-8")
    assert "camera-gear-btn" in src
    assert "⚙" in src


def test_gear_icon_opens_camera_settings() -> None:
    mgmt = CAM_MGMT.read_text(encoding="utf-8")
    grid = LIVE_GRID.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "onOpenCameraSettings" in mgmt
    assert "onOpenCameraSettings" in grid
    assert "onOpenCameraSettings" in app
    assert "camera-settings-modal" in html
    assert "camera-settings-modal" in app


def test_gear_menu_contains_required_actions() -> None:
    src = (
        CAM_MGMT.read_text(encoding="utf-8")
        + LIVE_GRID.read_text(encoding="utf-8")
        + INDEX.read_text(encoding="utf-8")
        + APP.read_text(encoding="utf-8")
    )
    # Camera settings reachable per-camera via the gear icon + modal.
    assert "camera-gear-btn" in src
    assert "onOpenCameraSettings" in src
    assert "camera-settings-modal" in src
    # Notification granularity reachable per-camera via the modal.
    assert "notification_min_threat" in src
    assert "notification_event_types" in src
    assert "notify_pass1" in src
    assert "notify_pass2" in src
    # Remove camera and reauthenticate available.
    assert "Remove camera" in src or "cs-remove-btn" in src or "camera-remove-btn" in src
    assert "Reauthenticate" in src or "cs-reauth-btn" in src or "camera-reauthenticate-btn" in src
