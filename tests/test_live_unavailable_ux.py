"""Source-level guards for graceful live failure and bounded retry.

Ring On-Demand and Wyze tiles can deliver no playable frames while the stream
API returns 5xx. The viewer must keep the snapshot visible, say so explicitly,
disable playback controls that cannot work, and stop the retry storm.
"""

from pathlib import Path

STATIC = Path("src/lookout/adapters/web/static")
HEALTH = STATIC / "js/utils/live-health.js"
PLAYER = STATIC / "js/components/live-player.js"
GRID = STATIC / "js/components/live-grid.js"
APP = STATIC / "js/app.js"
INDEX = STATIC / "index.html"
CSS = STATIC / "css/viewer.css"


def test_bounded_retry_helpers_are_shared() -> None:
    src = HEALTH.read_text(encoding="utf-8")
    assert "LIVE_MAX_ATTEMPTS = 3" in src
    assert "LIVE_RETRY_DELAYS_MS = [1000, 2500, 5000]" in src
    assert "SNAPSHOT_FALLBACK_POLL_MS = 2000" in src
    assert "isLiveHttpFailure(status)" in src
    assert "code >= 500" in src
    assert "retryDelayForAttempt(failedAttempts)" in src
    assert "Live unavailable — showing snapshot." in src
    assert "Ring On-Demand needs an active Ring plan" in src
    assert "Playback controls are disabled." in src

    for consumer in (PLAYER, GRID):
        js = consumer.read_text(encoding="utf-8")
        assert "liveUnavailableMessage" in js
        assert "retryDelayForAttempt" in js
        assert "SNAPSHOT_FALLBACK_POLL_MS" in js


def test_focus_player_detects_failed_live_and_offers_retry() -> None:
    src = PLAYER.read_text(encoding="utf-8")
    assert "startLive({ automatic = false } = {})" in src
    assert "LIVE_MEDIA_READY_TIMEOUT_MS" in src
    assert "this.video.videoWidth > 0" in src
    assert 'showSnapshotFallback("no playable video within 8s")' in src
    assert "showSnapshotFallback(`stream HTTP ${probe.status}`)" in src
    assert 'fetch(mp4Url, { method: "HEAD" })' in src
    assert 'this.startBtn.textContent = "↻ Retry live";' in src
    assert 'this.showSnapshotFallback("video error")' in src
    assert "this.startLive({ automatic: true })" in src
    assert "this.liveAttempts = 0" in src
    assert "hasPlayableMedia()" in src


def test_grid_tiles_show_status_disable_controls_and_stop_polling_storm() -> None:
    src = GRID.read_text(encoding="utf-8")
    assert "liveUnavailableMessage(cam, reason)" in src
    assert 'liveBtn.textContent = "↻ Retry live";' in src
    assert "retryDelayForAttempt(record.liveAttempts || 0)" in src
    assert "this.startTileLiveWebRTC(cam, video, overlay, streamSrc, { automatic: true })" in src
    assert "LIVE_MAX_ATTEMPTS" in src
    assert "record.snapshotPoll = setInterval(poll, SNAPSHOT_FALLBACK_POLL_MS)" in src
    assert "}, SNAPSHOT_FALLBACK_POLL_MS);" in src
    assert 'record.playBtn.setAttribute("aria-disabled", String(!playable));' in src
    assert 'record.muteBtn.setAttribute("aria-disabled", String(!playable));' in src
    assert "hasPlayableCamera()" in src
    assert "this._notifyLiveState();" in src


def test_app_disables_controls_when_no_media_is_playable() -> None:
    src = APP.read_text(encoding="utf-8")
    assert "_focusMediaAvailable()" in src
    assert 'this.playerPauseBtn.setAttribute("aria-disabled", String(!playable));' in src
    assert 'this.playerMuteBtn.setAttribute("aria-disabled", String(!playable));' in src
    assert "this.playerPauseBtn.disabled = !playable;" in src
    assert "this.playerMuteBtn.disabled = !playable;" in src
    assert "this.globalPauseUnavailable" in src
    assert "btn.disabled = !hasPlayable;" in src
    assert "this.livePlayer.liveAttempts = 0;" in src
    assert "onLiveStateChange: () =>" in src


def test_status_markup_and_styling_exist() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert 'id="live-unavailable-status"' in html
    assert 'role="status"' in html
    assert ".live-unavailable-status {" in css
    assert ".tile-live-status {" in css
    assert '.btn[aria-disabled="true"]' in css
