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
