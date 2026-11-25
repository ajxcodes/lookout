"""Source-level guards for session-only live pause/mute and compact controls.

Playback state is intentionally session-only: pausing freezes video.playback
without closing WebRTC, and a global pause can be exempted per camera. These
tests pin that contract in the viewer assets without needing a live camera.
"""

from pathlib import Path

APP = Path("src/lookout/adapters/web/static/js/app.js")
GRID = Path("src/lookout/adapters/web/static/js/components/live-grid.js")
PLAYER = Path("src/lookout/adapters/web/static/js/components/live-player.js")
EVENT_PLAYER = Path("src/lookout/adapters/web/static/js/components/event-player.js")
INDEX = Path("src/lookout/adapters/web/static/index.html")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")


def _fn_body(src: str, name: str) -> str:
    marker = f"  {name}("
    start = src.index(marker)
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unterminated function {name}")


# --- Pause state semantics ---------------------------------------------------


def test_grid_effective_pause_state_uses_global_and_overrides() -> None:
    src = GRID.read_text(encoding="utf-8")
    body = _fn_body(src, "isPaused")

    assert "return this.globalPaused" in body
    assert "!this.resumedSet.has(cameraId)" in body
    assert "this.explicitlyPausedSet.has(cameraId)" in body
    assert "this.globalPaused = false" in src
    assert "this.explicitlyPausedSet = new Set()" in src
    assert "this.resumedSet = new Set()" in src


def test_global_toggle_clears_overrides_and_applies_to_all_cameras() -> None:
    body = _fn_body(GRID.read_text(encoding="utf-8"), "setGlobalPaused")

    assert "this.globalPaused = Boolean(paused)" in body
    assert "this.explicitlyPausedSet.clear()" in body
    assert "this.resumedSet.clear()" in body
    assert "this.streamPlayers.forEach(" in body
    assert "this._applyCameraPause(cameraId)" in body


def test_per_camera_pause_uses_resume_exemption_while_globally_paused() -> None:
    body = _fn_body(GRID.read_text(encoding="utf-8"), "setPaused")

    assert "if (this.globalPaused)" in body
    assert "this.resumedSet.delete(cameraId)" in body
    assert "this.resumedSet.add(cameraId)" in body
    assert "this.explicitlyPausedSet.add(cameraId)" in body
    assert "this.explicitlyPausedSet.delete(cameraId)" in body


def test_pause_only_touches_video_playback_not_webrtc() -> None:
    grid = GRID.read_text(encoding="utf-8")
    body = _fn_body(grid, "_applyCameraPause")

    assert "record.video.pause()" in body
    assert "record.video.play().catch(" in body
    assert ".close()" not in body
    assert "record.pc" not in body
    assert "record.ws" not in body

    player = _fn_body(PLAYER.read_text(encoding="utf-8"), "setPaused")
    assert "this.video.pause()" in player
    assert "this.video.play().catch(" in player
    assert "stopLive" not in player


def test_lifecycle_resume_respects_pause_state() -> None:
    grid = GRID.read_text(encoding="utf-8")

    assert "!this.isPaused(cameraId)" in _fn_body(grid, "resumeVisibleStreams")
    assert "!this.isPaused(cameraId)" in _fn_body(grid, "resumeCameraStream")


# --- Per-tile overlay and tap semantics --------------------------------------


def test_tile_has_play_mute_overlay_and_tap_toggles_it() -> None:
    src = GRID.read_text(encoding="utf-8")

    assert 'streamControls.className = "tile-stream-controls"' in src
    assert "tile-play-btn" in src
    assert "tile-mute-btn" in src
    assert "this._revealTileControls(cam.id, true)" in src
    assert "this._revealTileControls(cam.id, false)" in src
    assert 'e.target.closest(".tile-stream-controls, .tile-live-btn")' in src
    assert "e.stopPropagation()" in src
    assert "}, 3000);" in src

    css = CSS.read_text(encoding="utf-8")
    assert ".tile-stream-controls.controls-visible" in css
    assert ".camera-grid-tile:hover .tile-stream-controls" in css
    assert ".camera-grid-tile:focus-within .tile-stream-controls" in css


def test_tile_mute_starts_muted_and_is_remembered_in_memory() -> None:
    src = GRID.read_text(encoding="utf-8")

    assert "this.cameraMutedState = new Map()" in src
    assert "this.cameraMutedState.get(cameraId) !== false" in _fn_body(src, "isMuted")
    assert "this.cameraMutedState.set(cameraId, Boolean(muted))" in src
    assert "record.video.muted = Boolean(muted)" in src
    assert "localStorage" not in _fn_body(src, "setMuted")


# --- Focus-player play/pause + mute -----------------------------------------


def test_focus_player_set_muted_does_not_renegotiate() -> None:
    player = PLAYER.read_text(encoding="utf-8")

    assert "this.muted = true" in player
    body = _fn_body(player, "setMuted")
    assert "this.video.muted = this.muted" in body
    assert "WebSocket" not in body
    assert "peerConnection" not in body
    assert "this.video.muted = this.muted" in player  # ontrack keeps the choice


def test_app_wires_pause_mute_to_active_media_and_global_button() -> None:
    app = APP.read_text(encoding="utf-8")

    assert "this.toggleGlobalPaused()" in app
    assert "this.togglePlayerPaused()" in app
    assert "this.togglePlayerMuted()" in app
    assert "this.eventPlayer.setPaused(this.eventPaused)" in app
    assert "this.eventPlayer.setMuted(this.eventMuted)" in app
    assert "this.livePlayer.setPaused(this.liveGrid.isPaused(this.activeCamera.id))" in app
    assert "this.liveGrid.setMuted(id, !this.liveGrid.isMuted(id))" in app
    assert "this.eventMutedByCamera = new Map()" in app
    assert "this.eventMutedByCamera.get(cameraId)" in app


def test_return_to_live_clears_all_pause_overrides() -> None:
    body = _fn_body(APP.read_text(encoding="utf-8"), "returnToLive")

    assert "this.liveGrid.setGlobalPaused(false)" in body
    assert "this.eventPaused = false" in body
    assert "this.livePlayer.setPaused(false)" in body
    assert 'this.setPlayerMode("live")' in body


def test_space_toggles_focused_player_or_tile_only() -> None:
    body = _fn_body(APP.read_text(encoding="utf-8"), "_handleSpaceKey")

    assert 'event.code !== "Space"' in body
    assert "active.closest(" in body
    assert "button, input, select, textarea, a[href]" in body
    assert 'active.closest(".camera-grid-tile")' in body
    assert 'active.closest("#video-surface, .video-container")' in body
    assert "this.toggleCameraPaused(tile.dataset.cameraId)" in body
    assert "this.togglePlayerPaused()" in body


def test_grid_tiles_are_keyboard_focusable() -> None:
    src = GRID.read_text(encoding="utf-8")

    assert "tile.tabIndex = 0" in src
    assert 'tile.setAttribute("aria-label"' in src


# --- Snapshot-only stage sizing ---------------------------------------------


def test_snapshot_only_events_size_stage_to_image() -> None:
    css = CSS.read_text(encoding="utf-8")
    src = EVENT_PLAYER.read_text(encoding="utf-8")

    assert ".video-container.snapshot-only .event-snapshot-card" in css
    assert "object-fit: contain" in css
    assert "max-height: min(70vh, 480px)" in css
    assert 'this.videoContainer.classList.add("snapshot-only")' in src
    assert 'this.videoContainer.classList.remove("snapshot-only")' in src
    assert "isSnapshotOnly()" in src
    assert 'id="download-snapshot-btn"' in INDEX.read_text(encoding="utf-8")
    assert 'id="inspect-frame-btn"' in INDEX.read_text(encoding="utf-8")
