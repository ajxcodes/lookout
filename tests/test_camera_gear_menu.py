from pathlib import Path

LIVE_GRID = Path("src/lookout/adapters/web/static/js/components/live-grid.js")
CAM_MGMT = Path("src/lookout/adapters/web/static/js/components/camera-management.js")


def test_camera_tile_has_always_visible_gear_icon() -> None:
    src = LIVE_GRID.read_text(encoding="utf-8")
    assert "camera-gear-btn" in src
    assert "⚙" in src


def test_gear_menu_contains_required_actions() -> None:
    src = CAM_MGMT.read_text(encoding="utf-8") + LIVE_GRID.read_text(encoding="utf-8")
    assert "Camera settings" in src or "camera settings" in src.lower()
    assert "Notification granularity" in src or "notification granularity" in src.lower()
    assert "Remove camera" in src
    assert "Reauthenticate" in src
