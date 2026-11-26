"""Source-level guards for the configurable focus-player layout.

The unified top control bar, the three acoustic display modes, the collapsed
events column, and the Settings "Focus view" controls are all persisted and
applied from the viewer assets; these checks keep those contracts explicit.
"""

import re
from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
APP = Path("src/lookout/adapters/web/static/js/app.js")
MODAL = Path("src/lookout/adapters/web/static/js/components/settings-modal.js")

OVERLAY_IDS = (
    "control-bar-scroll",
    "playback-essentials",
    "player-pause-btn",
    "player-mute-btn",
    "focus-control-overlay",
    "back-to-grid-btn",
    "focus-settings-btn",
    "fullscreen-btn",
    "maximize-btn",
    "return-to-live-btn",
    "player-controls",
    "speed-1x",
    "speed-15x",
    "speed-2x",
    "loop-toggle",
    "snapshot-playback-controls",
    "download-btn",
    "live-duration-badge",
    "focus-time-readout",
)


def _rule_body(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match is not None, f"missing CSS rule: {selector}"
    return match.group(1)


def test_control_overlay_keeps_all_existing_ids_without_duplicates() -> None:
    html = INDEX.read_text(encoding="utf-8")
    overlay_start = html.index('id="focus-control-overlay"')
    panel_start = html.index('class="acoustic-panel"')
    region = html[overlay_start:panel_start]

    assert html.index('id="video-container"') < overlay_start < panel_start
    for control_id in OVERLAY_IDS:
        needle = f'id="{control_id}"'
        assert needle in region, f"{control_id} missing from the unified overlay"
        assert region.count(needle) == 1, f"{control_id} duplicated"


def test_display_popover_hosts_all_view_state_controls() -> None:
    html = INDEX.read_text(encoding="utf-8")
    # The popover is header-anchored now, so it precedes the focus bar instead
    # of sitting inside its scroll row. Only the header/badge bounds matter.
    menu_start = html.index('id="display-menu"')
    menu_end = html.index('id="live-duration-badge"')
    assert menu_start < menu_end

    region = html[menu_start:menu_end]
    for control_id in (
        "acoustic-mode-control",
        "events-panel-toggle",
        "display-ai-details-toggle",
        "display-clock-pin-toggle",
    ):
        assert region.count(f'id="{control_id}"') == 1, f"{control_id} missing from Display popover"
        assert html.count(f'id="{control_id}"') == 1, f"{control_id} must not be duplicated"

    header = html[html.index('class="timeline-section-header"') : html.index('id="filter-bar"')]
    assert "events-panel-toggle" not in header, "timeline header keeps a plain title only"


def test_pinned_time_readout_overrides_bar_auto_hide() -> None:
    css = CSS.read_text(encoding="utf-8")
    rule = _rule_body(css, ".focus-control-overlay.controls-hidden.time-readout-pinned")
    assert "opacity: 1" in rule
    assert "visibility: visible" in rule
    assert "pointer-events: none" in rule
    assert "background-color: transparent" in rule
    assert (
        ".focus-control-overlay.controls-hidden.time-readout-pinned > :not(.focus-time-readout)"
        in css
    )


def test_control_overlay_is_translucent_theme_aware_and_top_pinned() -> None:
    css = CSS.read_text(encoding="utf-8")
    rule = _rule_body(css, ".focus-control-overlay")

    assert "position: absolute" in rule
    assert "top: 0" in rule
    assert "backdrop-filter: blur" in rule
    assert "color-mix(in srgb, var(--bg-surface)" in rule
    assert "z-index: 30" in rule
    assert "pointer-events: none" not in rule


def test_grid_tile_clock_keeps_its_absolute_badge() -> None:
    css = CSS.read_text(encoding="utf-8")
    rule = _rule_body(css, ".tile-clock-badge")
    assert "position: absolute" in rule
    assert "top: 14px" in rule and "right: 14px" in rule


def test_control_bar_placement_can_be_below_with_badge_still_on_top() -> None:
    css = CSS.read_text(encoding="utf-8")
    below = _rule_body(css, ".video-container.controls-below .focus-control-overlay")
    assert "position: static" in below

    badge = _rule_body(css, ".video-container.controls-below #live-duration-badge")
    assert "position: absolute" in badge
    assert "top:" in badge and "right:" in badge


def test_three_acoustic_modes_and_persistence_keys() -> None:
    css = CSS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    overlay = _rule_body(css, ".video-container.acoustic-overlay .acoustic-panel")
    assert "pointer-events: none" in overlay
    assert "backdrop-filter: blur" in overlay
    assert "bottom: 0" in overlay
    assert "display: none" in _rule_body(css, ".video-container.acoustic-collapsed .acoustic-panel")

    for key in (
        "lookout_focus_controls_overlay",
        "lookout_acoustic_mode",
        "lookout_acoustic_pinned",
        "lookout_events_collapsed",
    ):
        assert f'localStorage.setItem("{key}"' in app or f'getItem("{key}")' in app


def test_events_collapse_reclaims_full_width_and_hides_resize_handle() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert ".viewer-main:has(.timeline-section.collapsed)" in css
    single = _rule_body(css, ".viewer-main.events-collapsed")
    assert "grid-template-columns: minmax(0, 1fr)" in single

    handle = _rule_body(css, ".viewer-main.events-collapsed #events-resize-handle")
    assert "display: none" in handle


def test_settings_modal_exposes_focus_view_controls_and_keeps_focus_trap() -> None:
    html = INDEX.read_text(encoding="utf-8")
    modal = MODAL.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert 'id="section-focus"' in html
    assert "Focus view" in html
    for control_id in (
        "setting-focus-controls-overlay",
        "setting-acoustic-mode",
        "setting-events-collapsed",
    ):
        assert f'id="{control_id}"' in html
        assert f'querySelector("#{control_id}")' in modal

    assert "onFocusViewChanged" in modal
    assert "onFocusViewChanged:" in app
    assert 'addEventListener("change"' in modal
    assert "setFocusViewPreferences" in modal

    # New controls must not disturb the existing keyboard lifecycle.
    assert "_handleKeydown" in modal
    assert 'event.key === "Escape"' in modal
    assert "_trapFocus(event)" in modal


# --- Compact, auto-hiding, context-aware player bar --------------------------


COMPACT_BREAKPOINT = 960


def _compact_bar_media_block(css: str) -> str:
    """Return the media block that carries the 44px compact control bar."""
    blocks = re.findall(
        rf"(?ms)@media \(max-width: {COMPACT_BREAKPOINT}px\) \{{(.*?)(?=\n\}})", css
    )
    assert blocks, f"missing @media (max-width: {COMPACT_BREAKPOINT}px) media block"
    narrow = next((block for block in blocks if "height: 44px" in block), "")
    assert narrow, "missing compact control-bar rules in the narrow-screen media block"
    return narrow


def test_narrow_control_bar_is_one_scrollable_icon_row() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    narrow = _compact_bar_media_block(css)
    bar = _rule_body(css, ".focus-control-overlay")
    assert "flex-wrap: wrap" in bar

    assert "height: 44px" in narrow
    assert "flex-wrap: nowrap" in narrow
    assert "overflow-x: auto" in narrow
    assert "mask-image: linear-gradient" in narrow
    assert "content: attr(data-icon)" in narrow
    assert "font-size: 0" in narrow

    for control_id in ("back-to-grid-btn", "maximize-btn", "player-pause-btn", "player-mute-btn"):
        needle = f'id="{control_id}"'
        assert needle in html
        button = html[html.index(needle) - 60 : html.index(needle) + 200]
        assert "data-icon=" in button and "aria-label=" in button and "title=" in button


def test_compact_control_bar_breakpoint_covers_fold_inner_and_tablets() -> None:
    """The compact bar must also apply at 720px (Pixel 9 Pro Fold inner) and
    across the tablet range, not just phones. Desktop (>960px) keeps the full
    wrapped bar, so the compact rules must NOT be scoped to 640px any more.
    """
    css = CSS.read_text(encoding="utf-8")

    narrow = _compact_bar_media_block(css)
    assert "@media (max-width: 960px)" in css
    assert "height: 44px" in narrow
    assert "flex-wrap: nowrap" in narrow
    assert "overflow-x: auto" in narrow

    # The 44px icon-only bar must not be trapped in a 640px-only block.
    phone_blocks = re.findall(r"(?ms)@media \(max-width: 640px\) \{(.*?)(?=\n\})", css)
    assert not any("height: 44px" in block for block in phone_blocks)


def test_focus_stage_has_min_height_for_empty_selection() -> None:
    """Before any event is selected every child collapses; a min-height floor
    keeps #focus-stage a visible, usable surface (Playwright is_visible()).
    """
    css = CSS.read_text(encoding="utf-8")
    stage = _rule_body(css, ".focus-stage")
    match = re.search(r"min-height:\s*(\d+)px", stage)
    assert match, "focus stage needs a pixel min-height floor"
    assert int(match.group(1)) >= 200, "min-height floor is too small to be usable"

    narrow = _compact_bar_media_block(css)
    floor = re.search(r"\.focus-stage\s*\{[^}]*min-height:\s*(\d+)px", narrow)
    assert floor, "narrow layout must retain a min-height floor on the focus stage"
    assert int(floor.group(1)) >= 180


def test_control_bar_reveals_on_activity_and_hides_only_when_playing() -> None:
    app = APP.read_text(encoding="utf-8")

    assert "this.controlsIdleTimer = setTimeout(" in app
    assert "}, 3000);" in app
    assert "_autoHideAllowed()" in app
    assert "this.isPlayerPlaying()" in app
    assert "!this.controlsFocusWithin" in app
    assert "!this.controlsHover" in app
    assert "controls-hidden" in app
    assert 'setAttribute("aria-hidden", String(!this.controlsVisible))' in app


def test_surface_tap_toggles_bar_but_bar_clicks_keep_it_visible() -> None:
    app = APP.read_text(encoding="utf-8")

    assert 'this.videoSurface.addEventListener("click"' in app
    assert "this.toggleControls();" in app
    assert 'overlay.addEventListener("click", () => this.revealControls())' in app
    assert 'overlay.addEventListener("pointerenter"' in app
    assert 'overlay.addEventListener("pointerleave"' in app
    assert 'stage.addEventListener("focusin"' in app
    assert 'stage.addEventListener("pointermove"' in app


def test_bar_is_context_aware_with_one_shared_pause_and_mute_pair() -> None:
    css = CSS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert '.focus-control-overlay[data-mode="live"] .playback-toggles' in css
    assert '.focus-control-overlay[data-mode="live"] #video-download-wrap' in css
    assert 'setAttribute("data-mode"' in app
    assert 'mode === "event" ? "event" : "live"' in app

    assert html.count('id="player-pause-btn"') == 1
    assert html.count('id="player-mute-btn"') == 1
    assert 'id="playback-essentials"' in html


def test_global_pause_button_exposes_aria_pressed() -> None:
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert 'id="global-pause-btn"' in html
    assert 'aria-pressed="false"' in html
    assert 'btn.setAttribute("aria-pressed", String(paused))' in app
    assert "this.liveGrid.setGlobalPaused(!this.liveGrid.globalPaused)" in app


# --- Single three-state acoustic control + compact global pause --------------


def test_single_acoustic_control_exposes_three_pressed_states() -> None:
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert 'id="acoustic-mode-control"' in html
    assert 'role="group"' in html
    for mode in ("overlay", "below", "hidden"):
        needle = f'id="acoustic-mode-{mode}"'
        assert needle in html
        button = html[html.index(needle) - 120 : html.index(needle) + 240]
        assert f'data-mode="{mode}"' in button
        assert "aria-pressed=" in button
        assert "title=" in button

    assert "this.acousticModeButtons.forEach((btn)" in app
    assert 'btn.addEventListener("click"' in app
    assert 'if (mode !== "overlay" && mode !== "below" && mode !== "hidden") return;' in app
    assert "this.acousticModeControl.dataset.mode = prefs.acousticMode;" in app
    assert 'localStorage.setItem("lookout_acoustic_mode"' in app


def test_compact_global_pause_is_icon_only_at_960px() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    narrow = _compact_bar_media_block(css)
    assert "#global-pause-btn" in narrow
    assert "width: 32px" in narrow
    assert "font-size: 0" in narrow
    assert "attr(data-icon)" in narrow

    btn = html[
        html.index('id="global-pause-btn"') - 120 : html.index('id="global-pause-btn"') + 240
    ]
    assert "aria-label=" in btn and "title=" in btn and "data-icon=" in btn
    assert 'btn.dataset.icon = paused ? "▶" : "⏸";' in app


def test_snapshot_fallback_disables_pause_and_mute_controls() -> None:
    app = APP.read_text(encoding="utf-8")
    grid = Path("src/lookout/adapters/web/static/js/components/live-grid.js").read_text(
        encoding="utf-8"
    )
    css = CSS.read_text(encoding="utf-8")

    assert "_focusMediaAvailable()" in app
    assert 'this.playerPauseBtn.setAttribute("aria-disabled", String(!playable));' in app
    assert 'this.playerMuteBtn.setAttribute("aria-disabled", String(!playable));' in app
    assert "this.liveGrid.setGlobalPaused(!this.liveGrid.globalPaused)" in app
    assert "hasPlayableCamera()" in app
    assert 'record.playBtn.setAttribute("aria-disabled", String(!playable));' in grid
    assert 'record.muteBtn.setAttribute("aria-disabled", String(!playable));' in grid
    assert '.btn[aria-disabled="true"]' in css
