"""TR-15: the reusable viewport audit is committed and parameterized."""

import ast
import re
from pathlib import Path

AUDIT = Path("scripts/lookout_viewport_audit.py")
UX_SUITE = Path("scripts/lookout_ux_suite.py")

INTERACTION_STEPS = (
    "camera chip multi-select toggles grid tiles",
    "grid/focus view mode toggle",
    "event card selection opens detail player",
    "settings modal opens/closes",
    "add-camera dropdown opens/closes",
    "events panel collapse/expand",
    "camera settings modal opens/closes",
    "keyboard tab focus is visible",
    "settings modal traps focus",
)


def test_audit_script_is_committed_and_parameterized() -> None:
    assert AUDIT.exists(), "scripts/lookout_viewport_audit.py must be committed"
    src = AUDIT.read_text(encoding="utf-8")

    for flag in ("--base-url", "--out-dir", "--ingress-token"):
        assert flag in src, flag
    assert "events_jump_control" in src

    match = re.search(r"(?ms)^VIEWPORTS:.*?=\s*\[(.*?)^\]", src)
    assert match, "missing VIEWPORTS declaration"
    assert len(re.findall(r'^\s+\("', match.group(1), re.M)) == 15

    for step in INTERACTION_STEPS:
        assert f'step("{step}"' in src, step

    # The events toggle moved into the Display popover but keeps its ID.
    assert "#display-menu-btn" in src
    assert "#display-menu:not([hidden])" in src


def test_focus_view_phase_covers_both_pixel_fold_viewports() -> None:
    src = AUDIT.read_text(encoding="utf-8")

    assert "FOCUS_VIEW_NAMES" in src
    assert "pixel_pro_fold_384x854_cover" in src
    assert "pixel_pro_fold_720x748_inner" in src
    assert "FOCUS_VIEW_VIEWPORTS" in src
    assert "def audit_focus_view(" in src
    assert "app.playEvent(ev)" in src
    assert "focus_{name}.png" in src
    assert "PHASE 3: FOCUS VIEW (PIXEL 9 PRO FOLD)" in src
    assert 'report["focus_view"]' in src


def test_focus_view_checks_are_separate_from_existing_denominators() -> None:
    src = AUDIT.read_text(encoding="utf-8")

    for needle in (
        "control_bar_height_le_48",
        "controls_not_clipped",
        "video_surface_height_ge_250",
        "global_pause_present",
        "global_pause_toggles",
        '"focus_view_checks_total"',
        '"focus_view_checks_failed"',
        'probe["bar_height"] <= 48',
        'probe["surface_height"] >= 250',
        'probe["controls_right_max"] <= width + 1',
    ):
        assert needle in src, needle

    summary = src[src.index('report["summary"] = {') :]
    assert "viewports_total" in summary
    assert "interaction_steps_total" in summary
    assert "focus_view_checks_total" in summary
    assert "focus_view_total" in summary


def test_modal_focus_trap_guards_close_button_click() -> None:
    """Escape now closes the dialog; clicking the hidden close button must be conditional."""
    src = AUDIT.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^    def modal_focus_trap\(\).*?(?=^    def |\Z)", src)
    assert match, "missing modal_focus_trap() function"
    lines = match.group(0).splitlines()

    click_idx = next(
        (i for i, line in enumerate(lines) if 'page.locator("#modal-close-btn").click()' in line),
        None,
    )
    assert click_idx is not None, "close-button click missing from modal_focus_trap()"
    click_indent = len(lines[click_idx]) - len(lines[click_idx].lstrip())

    # The nearest preceding line at a lower indentation is the enclosing block header.
    guard = None
    for line in reversed(lines[:click_idx]):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= click_indent:
            continue
        guard = stripped
        break

    assert guard == "if not escape_closed:", (
        f"close-button click must run only when Escape left the dialog open, "
        f"found enclosing block {guard!r}"
    )


def test_ux_suite_is_committed_parses_and_keeps_audit_denominators() -> None:
    """The new UX suite exists, parses, and reports its own check denominators."""
    assert UX_SUITE.exists(), "scripts/lookout_ux_suite.py must be committed"
    src = UX_SUITE.read_text(encoding="utf-8")
    ast.parse(src)  # import-free syntax check (Playwright is a dev-only extra)

    for flag in ("--base-url", "--out-dir", "--ingress-token"):
        assert flag in src, flag
    assert "lookout-ux-report.json" in src

    for width in (320, 360, 375, 384, 720, 1280):
        assert f"({width}," in src or f"{width}x" in src
    for check in (
        "header_overflow_",
        "control_bar_height_le_48_",
        "tap_toggles_control_bar_",
        "pause_mute_disabled_without_media",
        "pause_mute_state_transitions",
        'f"acoustic_mode_{mode}"',
        'for mode in ("overlay", "below", "hidden")',
        "events_collapse_reclaims_width",
        "settings_persist_acoustic_across_reload",
        "jump_to_events_visible_and_expands",
        "chip_scroll_affordance",
        "display_menu_opens_with_keyboard",
        "display_menu_escape_closes_and_restores_focus",
        "display_menu_outside_click_closes",
        "display_menu_controls_present",
        "display_ai_disabled_in_live",
        "display_ai_enabled_in_event",
        "display_time_readout_clock",
        "display_time_readout_event_time",
        "display_time_readout_shows_home_zone",
        "display_clock_pin_persists",
        "display_clock_pin_survives_bar_hide",
        "display_clock_unpinned_hides_with_bar",
        "snapshot_fallback_status_visible",
        "no_unhandled_js_exceptions",
        "ingress_api_requests_prefixed",
    ):
        assert check in src, check

    assert "def open_display_menu(" in src
    assert 'report["phases"]["display"]' in src

    # The UX report uses separate keys; the viewport audit's 15/18 wording is
    # never changed by the follow-up suite.
    assert "ux_viewports_total" in src
    assert "ux_checks_total" in src
    assert '"viewports_total"' not in src
    assert '"interaction_steps_total"' not in src
    audit = AUDIT.read_text(encoding="utf-8")
    assert "viewports_total" in audit
    assert "interaction_steps_total" in audit


def test_ux_suite_uses_computed_safe_tap_point() -> None:
    """Tap-to-toggle must hit bare surface, not a fixed point under the header/bar."""
    src = UX_SUITE.read_text(encoding="utf-8")

    # Regression guard: the old fixed element-relative point is gone.
    assert 'position={"x": 20, "y": 20}' not in src

    assert "SURFACE_TAP_POINT_JS" in src
    assert "def tap_video_surface(" in src
    assert "page.mouse.click(" in src
    # The point clears both overlapping bars and stays inside the surface.
    assert "'.app-header'" in src
    assert "'#focus-control-overlay'" in src
    assert "s.left + 1, s.right - 1" in src
    assert "s.top + 1, s.bottom - 1" in src

    match = re.search(r"(?ms)^def phase_control_bar\(.*?(?=^def |\Z)", src)
    assert match, "missing phase_control_bar() function"
    body = match.group(0)
    assert body.count("tap_video_surface(page)") == 2
    assert ".click(" not in body  # no fixed-position locator clicks in this phase
    assert 'f"tap_toggles_control_bar_{width}"' in body
