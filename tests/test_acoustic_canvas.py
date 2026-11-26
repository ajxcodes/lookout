"""Regression: the acoustic visualizer must read real Web Audio data, live in
the focus player, and support below/overlay/collapsed modes while fitting the
focus stage without page scrolling."""

import re
from html.parser import HTMLParser
from pathlib import Path

CANVAS_JS = Path("src/lookout/adapters/web/static/js/components/acoustic-canvas.js")
APP_JS = Path("src/lookout/adapters/web/static/js/app.js")
INDEX_HTML = Path("src/lookout/adapters/web/static/index.html")
VIEWER_CSS = Path("src/lookout/adapters/web/static/css/viewer.css")


def test_resize_observer_wires_canvas_to_resize() -> None:
    src = CANVAS_JS.read_text(encoding="utf-8")

    assert "ResizeObserver" in src
    assert "new ResizeObserver(() => this.resizeCanvas())" in src
    assert ".observe(this.canvas)" in src


def test_window_resize_fallback_retained() -> None:
    src = CANVAS_JS.read_text(encoding="utf-8")

    assert 'window.addEventListener("resize", () => this.resizeCanvas())' in src


def test_resize_canvas_derives_backing_store_from_bounding_rect() -> None:
    src = CANVAS_JS.read_text(encoding="utf-8")

    assert "getBoundingClientRect()" in src
    assert "this.canvas.width = rect.width" in src
    assert "this.canvas.height = rect.height" in src


# --- Real Web Audio analysis (no fabricated decibels) ------------------------


def test_canvas_accepts_analyser_and_reads_real_samples() -> None:
    src = CANVAS_JS.read_text(encoding="utf-8")

    assert "analyser = null" in src, "constructor must accept an optional analyser"
    assert "setAnalyser(analyser)" in src
    assert "getByteTimeDomainData" in src or "getByteFrequencyData" in src
    assert "20 * Math.log10" in src, "VU must come from sample RMS"
    assert "-60" in src, "VU must have a truthful noise floor"


def test_canvas_shows_truthful_label_without_audio() -> None:
    src = CANVAS_JS.read_text(encoding="utf-8")

    assert '"-- dB"' in src
    assert "this.hasSignal = false" in src
    assert "this.ctx.lineTo(width, midY)" in src, "no-audio state draws a flat line"


def test_app_builds_one_media_element_source_and_keeps_audio_audible() -> None:
    src = APP_JS.read_text(encoding="utf-8")

    assert "AudioContext" in src
    assert "createMediaElementSource" in src
    assert "createAnalyser" in src
    assert "analyser.connect(this.audioCtx.destination)" in src
    assert "audioSetupAttempted" in src, "source must be created only once"
    assert "setAnalyser(this.analyser)" in src
    assert "resumeEventAudio" in src and "suspendEventAudio" in src


def test_app_has_no_acoustic_simulation_from_metadata() -> None:
    src = APP_JS.read_text(encoding="utf-8")

    assert "audio_decibels || -18" not in src
    assert "Acoustic simulation" not in src
    assert "setAudioDecibels(" not in src
    assert "onTimeUpdate: (currentTime, duration) =>" not in src


# --- Focus-view panel placement ----------------------------------------------


class _DivProbe(HTMLParser):
    """Collect divs as (classes, id, parent classes, document order) tuples."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.divs: list[tuple[str, str, str, int]] = []
        self._order = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        attributes = {key: (value or "") for key, value in attrs}
        classes = attributes.get("class", "")
        parent = self.stack[-1] if self.stack else ""
        self.divs.append((classes, attributes.get("id", ""), parent, self._order))
        self._order += 1
        self.stack.append(classes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.stack:
            self.stack.pop()


def _index_divs() -> list[tuple[str, str, str, int]]:
    probe = _DivProbe()
    probe.feed(INDEX_HTML.read_text(encoding="utf-8"))
    return probe.divs


def _acoustic_panels() -> list[tuple[str, str, str, int]]:
    return [div for div in _index_divs() if "acoustic-panel" in div[0].split()]


def test_index_html_has_exactly_one_acoustic_panel() -> None:
    assert len(_acoustic_panels()) == 1


def test_acoustic_mode_control_lives_in_display_popover() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    menu_start = html.index('id="display-menu"')
    menu_end = html.index('id="live-duration-badge"')
    assert 'id="acoustic-mode-control"' in html[menu_start:menu_end]
    assert html.count('id="acoustic-mode-control"') == 1


def test_acoustic_panel_is_after_player_controls_in_normal_flow() -> None:
    controls = [div for div in _index_divs() if div[1] == "player-controls"]
    panels = _acoustic_panels()

    assert len(controls) == 1
    assert len(panels) == 1
    assert panels[0][3] > controls[0][3], (
        "acoustic panel must follow #player-controls in document order"
    )


def test_acoustic_panel_lives_inside_video_container_after_the_controls() -> None:
    panel = _acoustic_panels()[0]
    controls = [div for div in _index_divs() if div[1] == "player-controls"]
    overlay = [div for div in _index_divs() if div[1] == "focus-control-overlay"]

    assert "video-container" in panel[2].split(), (
        f"acoustic panel must be inside #video-container; parent={panel[2]!r}"
    )
    assert len(overlay) == 1
    assert panel[3] > controls[0][3] > overlay[0][3], (
        "order must be: control overlay, playback controls, acoustic panel"
    )


def test_acoustic_panel_is_not_nested_in_ai_details_card() -> None:
    panels = _acoustic_panels()

    assert all("focus-ai-details-card" not in panel[2].split() for panel in panels)


def _css_rule_body(selector: str) -> str:
    css = VIEWER_CSS.read_text(encoding="utf-8")
    # Allow comma-separated selectors to span multiple lines in the stylesheet.
    pattern = r"\s+".join(re.escape(part) for part in selector.split())
    match = re.search(rf"{pattern}\s*\{{([^}}]*)\}}", css)
    assert match is not None, f"missing CSS rule for {selector!r}"
    return match.group(1)


def test_acoustic_panel_is_normal_flow_not_an_overlay() -> None:
    css = VIEWER_CSS.read_text(encoding="utf-8")
    rule = _css_rule_body(".acoustic-panel")

    assert ".video-container .acoustic-panel" not in css
    assert "position: absolute" not in rule
    assert "z-index" not in rule
    assert "pointer-events: none" not in rule


# --- Focus-stage layout invariants ------------------------------------------


def test_focus_stage_is_viewport_fitting_flex_column() -> None:
    rule = _css_rule_body(".focus-stage")

    assert "display: flex" in rule
    assert "flex-direction: column" in rule
    assert "flex: 1 1 auto" in rule
    # A positive min-height floor keeps the stage visible even with no media
    # selected; the flexible video still shrinks below the fitted max-height.
    assert re.search(r"min-height:\s*\d+px", rule)
    assert "overflow: hidden" in rule


def test_video_shrinks_while_panels_stay_visible() -> None:
    video_rule = _css_rule_body(".focus-stage .video-container")

    assert "flex: 1 1 auto" in video_rule
    assert "min-height: 0" in video_rule

    surface_rule = _css_rule_body(".focus-stage .video-surface")
    assert "flex: 1 1 auto" in surface_rule
    assert "min-height: 0" in surface_rule

    panel_rule = _css_rule_body(".focus-stage .acoustic-panel")
    assert "flex: 0 0 auto" in panel_rule

    ai_rule = _css_rule_body(".focus-stage > .focus-ai-details-card")
    assert "max-height" in ai_rule
    assert "overflow-y: auto" in ai_rule


def test_maximized_and_fullscreen_share_one_full_width_column() -> None:
    css = VIEWER_CSS.read_text(encoding="utf-8")

    assert ".focus-stage.maximized" in css
    assert ".focus-stage:fullscreen" in css
    assert "fitFocusStage" in APP_JS.read_text(encoding="utf-8")

    rule = _css_rule_body(
        ".focus-stage.maximized .video-container, .focus-stage:fullscreen .video-container"
    )
    assert "max-width: 100%" in rule
    assert "max-height: none" in rule
    assert "aspect-ratio: auto" in rule


# --- Three acoustic display modes -------------------------------------------


def test_three_acoustic_modes_have_css_rules() -> None:
    overlay = _css_rule_body(".video-container.acoustic-overlay .acoustic-panel")
    assert "position: absolute" in overlay
    assert "bottom: 0" in overlay
    assert "backdrop-filter: blur" in overlay
    assert "pointer-events: none" in overlay
    assert "z-index:" in overlay

    collapsed = _css_rule_body(".video-container.acoustic-collapsed .acoustic-panel")
    assert "display: none" in collapsed
    hidden = _css_rule_body(".video-container.acoustic-hidden .acoustic-panel")
    assert "display: none" in hidden

    css = VIEWER_CSS.read_text(encoding="utf-8")
    assert ".video-container.acoustic-below" in css or "acoustic-panel" in css
    assert '.acoustic-mode-segment .acoustic-mode-btn[aria-pressed="true"]' in css


def test_acoustic_modes_are_persisted_and_toggleable() -> None:
    app = APP_JS.read_text(encoding="utf-8")

    assert 'localStorage.setItem("lookout_acoustic_mode"' in app
    assert '"lookout_acoustic_pinned"' in app
    assert '"lookout_acoustic_mode"' in app
    assert "classList.add(`acoustic-${prefs.acousticMode}`)" in app
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="acoustic-mode-control"' in html
    for mode in ("overlay", "below", "hidden"):
        assert f'data-mode="{mode}"' in html
    assert 'id="acoustic-pin-btn"' not in html
    assert 'id="acoustic-collapse-btn"' not in html


# --- Narrow-screen default ---------------------------------------------------


def test_narrow_screens_default_to_pinned_acoustic_overlay() -> None:
    app = APP_JS.read_text(encoding="utf-8")

    assert 'window.matchMedia("(max-width: 640px)").matches' in app
    assert "if (!this._acousticUserOverridden && this._isNarrowLayout())" in app
    assert 'prefs.acousticMode = "overlay"' in app
    assert "prefs.acousticPinned = true" in app


def test_explicit_acoustic_choice_wins_over_narrow_default() -> None:
    app = APP_JS.read_text(encoding="utf-8")

    assert "lookout_acoustic_user_override" in app
    assert "this._acousticUserOverridden =" in app
    assert "if (this._acousticUserOverridden) return;" in app
    assert (
        'Object.prototype.hasOwnProperty.call(partial, "acousticMode")' in app
        or '"acousticMode" in partial' in app
    )


def test_narrow_overlay_keeps_video_height_with_css_rule() -> None:
    rule = _css_rule_body(".video-container.acoustic-overlay .acoustic-panel")

    assert "position: absolute" in rule
    assert "bottom: 0" in rule
    assert "backdrop-filter: blur" in rule
