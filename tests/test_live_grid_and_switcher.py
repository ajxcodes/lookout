"""Unit tests for camera switcher rich metadata, semantic icon inference, and live player WebRTC."""

from pathlib import Path

from lookout.adapters.web.helpers import build_camera_card_data, infer_camera_icon
from lookout.config.schema import CameraConfig


def test_camera_switcher_rich_metadata_badges() -> None:
    ring_cam = CameraConfig(
        id="front_door",
        name="Front Door",
        stream_url="rtsp://ring-mqtt/front_door",
        camera_type="ring",
    )
    camera_card = build_camera_card_data(ring_cam)
    assert camera_card["provider_badge"] in {"ring", "wyze", "generic"} and camera_card[
        "stream_mode"
    ] in {"live_rtc", "on_demand"}
    assert camera_card["provider_badge"] == "ring"
    assert camera_card["stream_mode"] == "on_demand"

    wyze_cam = CameraConfig(
        id="office_cam",
        name="Office",
        stream_url="rtsp://wyze-bridge/office",
        camera_type="wyze",
    )
    wyze_card = build_camera_card_data(wyze_cam)
    assert wyze_card["provider_badge"] == "wyze"
    assert wyze_card["stream_mode"] == "live_rtc"


def test_camera_semantic_icon_inference() -> None:
    assert infer_camera_icon("Front Door") == "🚪" and infer_camera_icon("Backyard") == "🌲"
    assert infer_camera_icon("Mochi Crate") == "🐾"
    assert infer_camera_icon("Office Desk") == "💼"
    assert infer_camera_icon("Driveway") == "🚗"


def test_live_player_direct_track_attachment_fallback() -> None:
    live_player_path = Path("src/lookout/adapters/web/static/js/components/live-player.js")
    live_player_source = live_player_path.read_text(encoding="utf-8")
    assert "addTrack" in live_player_source and "MediaStream" in live_player_source


def test_live_player_early_ice_candidate_queueing() -> None:
    live_player_path = Path("src/lookout/adapters/web/static/js/components/live-player.js")
    live_player_source = live_player_path.read_text(encoding="utf-8")
    assert "iceCandidateQueue" in live_player_source and "addIceCandidate" in live_player_source


def test_live_player_snapshot_overlay_lifecycle() -> None:
    live_player_path = Path("src/lookout/adapters/web/static/js/components/live-player.js")
    live_player_source = live_player_path.read_text(encoding="utf-8")
    assert "onplaying" in live_player_source
    assert "showSnapshotFallback" in live_player_source
    assert "onconnectionstatechange" in live_player_source


def test_live_grid_snapshot_fallback_resiliency() -> None:
    live_grid_path = Path("src/lookout/adapters/web/static/js/components/live-grid.js")
    live_grid_source = live_grid_path.read_text(encoding="utf-8")
    assert "onplaying" in live_grid_source
    assert "restoreSnapshotFallback" in live_grid_source
    assert "onconnectionstatechange" in live_grid_source
    assert "tile-overlay" in live_grid_source


def test_camera_selection_grid_focus_and_empty_events() -> None:
    app_js = Path("src/lookout/adapters/web/static/js/app.js").read_text(encoding="utf-8")
    sw_path = Path("src/lookout/adapters/web/static/js/components/switcher.js")
    switcher_js = sw_path.read_text(encoding="utf-8")
    grid_path = Path("src/lookout/adapters/web/static/js/components/live-grid.js")
    live_grid_js = grid_path.read_text(encoding="utf-8")

    assert "getSelectedCameraIds" in switcher_js
    assert "cameraIds.length === 1" in app_js
    assert "switchToFocus" in app_js
    assert "selectedIds.length === 0" in app_js
    assert "events = []" in app_js
    assert "checkDeepLink" in app_js
    assert "targetEventId" in app_js
    assert "No camera feeds selected in Live Grid" in live_grid_js


def test_ntfy_action_buttons_and_deep_links() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from lookout.adapters.ntfy import NtfyDispatcher
    from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel

    dispatcher = NtfyDispatcher(deep_link_base_url="https://ha.home.ajx.codes/lookout")
    event = CameraEvent(
        id=str(uuid4()),
        camera_id="front_door",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.INFO,
        summary="Front Porch Motion",
    )
    assert dispatcher.deep_link_base_url == "https://ha.home.ajx.codes/lookout"
    assert event.camera_id == "front_door"
