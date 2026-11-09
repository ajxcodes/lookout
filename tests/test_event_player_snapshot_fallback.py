"""Unit tests for snapshot-only event presentation, event-player fallback, and timeline badging."""

from pathlib import Path


def test_event_player_snapshot_card_and_controls_adaptation() -> None:
    index_html = Path("src/lookout/adapters/web/static/index.html").read_text(encoding="utf-8")
    event_player_source = Path(
        "src/lookout/adapters/web/static/js/components/event-player.js"
    ).read_text(encoding="utf-8")

    assert "event-snapshot-card" in index_html and "📸 Snapshot" in event_player_source
    assert "downloadSnapshot" in event_player_source
    assert "inspectFrame" in event_player_source

    # Event Player Parity: videoUrl resolution hierarchy
    assert "eventItem.media_uri" in event_player_source
    assert "eventItem.clip_url" in event_player_source
    assert "eventItem.video_url" in event_player_source
    assert "`/media/events/${eventItem.id}.mp4`" in event_player_source

    # Autoplay catch muting and retrying play
    assert "this.video.muted = true" in event_player_source
    assert "this.video.play().catch" in event_player_source

    # Snapshot image resolution hierarchy
    assert "eventItem.snapshot_url" in event_player_source
    assert "eventItem.snapshot_uri" in event_player_source
    assert "eventItem.gif_url" in event_player_source


def test_timeline_media_type_badges() -> None:
    timeline_source = Path("src/lookout/adapters/web/static/js/components/timeline.js").read_text(
        encoding="utf-8"
    )

    assert "media-badge-snapshot" in timeline_source and "media-badge-video" in timeline_source
    assert "📸 Snapshot" in timeline_source
