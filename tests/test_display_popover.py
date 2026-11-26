"""Focused guards for the unified Display popover and pinnable time readout.

Source-level checks keep the deterministic contracts explicit: the popover is
keyboard accessible, hosts every moved/new view-state control, and the clock
readout reuses the existing timezone/timeline helpers without new dependencies.
"""

import re
from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
APP = Path("src/lookout/adapters/web/static/js/app.js")
TZ = Path("src/lookout/adapters/web/static/js/utils/timezone.js")
TIMELINE = Path("src/lookout/adapters/web/static/js/components/timeline.js")
UX_SUITE = Path("scripts/lookout_ux_suite.py")

# The readout is a theme-independent video-player OSD: fixed light foregrounds
# on the pill's fixed dark background, in both colour schemes.
READOUT_FOREGROUNDS = {
    ".focus-time-readout": "#f8fafc",
    ".focus-clock-home": "#cbd5e1",
    ".focus-event-time": "#93c5fd",
    ".focus-event-age": "#cbd5e1",
}
READOUT_PILL_BG = "#0f172a"  # rgb(15, 23, 42): base of rgba(15, 23, 42, 0.6)
THEME_TEXT_TOKENS = ("var(--text-main)", "var(--text-muted)", "var(--accent-primary)")


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    light, dark = _luminance(fg), _luminance(bg)
    return (max(light, dark) + 0.05) / (min(light, dark) + 0.05)


def test_display_trigger_is_accessible_and_icon_only_on_narrow_screens() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    needle = 'id="display-menu-btn"'
    button = html[html.index(needle) - 120 : html.index(needle) + 260]
    assert 'id="display-menu"' in html
    assert 'aria-controls="display-menu"' in button
    assert 'aria-expanded="false"' in button
    assert "aria-label=" in button and "title=" in button and "data-icon=" in button
    assert "Display options" in button

    narrow = css[css.index("@media (max-width: 960px)") :]
    assert ".focus-control-overlay .btn," in narrow
    assert "font-size: 0" in narrow
    assert "content: attr(data-icon)" in narrow


def test_popover_is_outside_the_scroll_row_and_uses_hidden_attribute() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    scroll_start = html.index('id="control-bar-scroll"')
    menu_start = html.index('id="display-menu"')
    badge_start = html.index('id="live-duration-badge"')
    assert scroll_start < menu_start < badge_start
    menu = html[menu_start:badge_start]
    assert 'id="display-menu-btn"' not in menu
    assert 'role="dialog"' in menu and 'aria-label="Display options"' in menu
    assert "hidden" in menu.split(">")[0]

    assert ".display-menu[hidden] {" in css
    hidden_rule = css[css.index(".display-menu[hidden] {") :]
    assert "display: none" in hidden_rule[:80]


def test_ai_toggle_disabled_in_live_with_events_only_hint() -> None:
    app = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    needle = 'id="display-ai-details-toggle"'
    button = html[html.index(needle) - 60 : html.index(needle) + 220]
    assert 'aria-pressed="true"' in button
    assert "aria-label=" in button or "title=" in button

    assert '"Available for events only"' in app
    assert "this.displayAiDetailsToggle.disabled = !available;" in app
    assert "this._displayModeIsEvent()" in app
    assert "lookout_display_ai_details" in app
    assert 'classList.toggle("ai-details-hidden", !show)' in app

    css = CSS.read_text(encoding="utf-8")
    assert ".focus-ai-details-card.ai-details-hidden {" in css
    assert "display: none !important" in css


def test_clock_pin_persists_and_overrides_bar_hide() -> None:
    app = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert '"lookout_display_clock_pinned"' in app
    assert 'localStorage.setItem(\n        "lookout_display_clock_pinned"' in app
    assert 'classList.toggle("time-readout-pinned"' in app
    assert "!this.displayMenuOpen" in app
    assert "setInterval(() => this.updateTimeReadout(), 1000)" in app
    pinned = css[css.index(".focus-control-overlay.controls-hidden.time-readout-pinned {") :]
    assert "opacity: 1" in pinned[:200]
    assert "visibility: visible" in pinned[:200]
    assert "> :not(.focus-time-readout)" in pinned[:600]


def test_readout_reuses_timezone_and_timeline_helpers() -> None:
    app = APP.read_text(encoding="utf-8")
    tz = TZ.read_text(encoding="utf-8")
    timeline = TIMELINE.read_text(encoding="utf-8")

    for helper in (
        "eventTimeWithLocalHint",
        "formatInBrowserZone",
        "formatInDisplayZone",
        "formatLocalClockTitle",
        "getDisplayTimezone",
        "setDisplayTimezone",
    ):
        assert helper in app, f"app.js must import {helper}"
        assert f"export function {helper}" in tz or f"export const {helper}" in tz

    assert "this.timeline.formatRelativeTime(item.timestamp)" in app
    assert "formatRelativeTime(isoString)" in timeline
    assert "_setReadoutText(this.focusEventTime, info.primary)" in app
    assert "_setReadoutText(" in app and ".readout-value" in app
    html = INDEX.read_text(encoding="utf-8")
    for label in ("Local", "Home", "Event"):
        assert f'<span class="readout-label">{label}</span>' in html
    assert 'getElementById("focus-time-readout")' in app
    assert 'id="focus-time-readout"' in INDEX.read_text(encoding="utf-8")


def test_ux_suite_adds_deterministic_display_checks() -> None:
    src = UX_SUITE.read_text(encoding="utf-8")
    for check in (
        "display_menu_opens_with_keyboard",
        "display_menu_escape_closes_and_restores_focus",
        "display_menu_outside_click_closes",
        "display_menu_controls_present",
        "display_ai_disabled_in_live",
        "display_ai_enabled_in_event",
        "display_time_readout_clock",
        "display_time_readout_event_time",
        "display_time_readout_shows_home_zone",
        "display_clock_pin_survives_bar_hide",
    ):
        assert f'"{check}"' in src, check
    assert "DISPLAY_PROBE_JS" in src
    assert "def phase_display(" in src
    assert 'report["phases"]["display"]' in src


def test_readout_uses_fixed_light_osd_colors_not_theme_tokens() -> None:
    """The dark pill never changes with the theme, so its text must not either."""
    css = CSS.read_text(encoding="utf-8")

    for selector, color in READOUT_FOREGROUNDS.items():
        body = _rule(css, selector)
        assert f"color: {color};" in body, f"{selector} must use fixed {color}"
        for token in THEME_TEXT_TOKENS:
            assert token not in body, f"{selector} must not use {token}"

    # Layout/behaviour stays untouched: the pill keeps its translucent dark
    # background rather than picking up a theme-driven fill.
    pill = _rule(css, ".focus-time-readout")
    assert "background: rgba(15, 23, 42, 0.6);" in pill


def test_readout_foregrounds_meet_wcag_aa_on_dark_pill() -> None:
    """Both colour schemes share one fixed foreground/background pair.

    Because the OSD colours are theme-independent, a single contrast check
    against the pill's dark base covers light and dark contexts alike.
    """
    for selector, color in READOUT_FOREGROUNDS.items():
        ratio = _contrast(color, READOUT_PILL_BG)
        assert ratio >= 4.5, f"{selector}: {color} on {READOUT_PILL_BG} = {ratio:.2f}:1"
