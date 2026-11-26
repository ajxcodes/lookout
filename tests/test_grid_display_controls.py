"""TR-10…TR-14: the one Display popover is header-anchored and phone-reachable.

Source-level guards keep the Grid/mobile contracts explicit: a single
header-anchored trigger/panel pair, the four view-state controls hosted once,
no inline font override on the view-mode buttons, a reachable density picker at
<=640px, non-shrinking camera chips with an intact scroll affordance, and the
shared preference mirror applied on Grid entry.
"""

import re
from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
APP = Path("src/lookout/adapters/web/static/js/app.js")
SWITCHER = Path("src/lookout/adapters/web/static/js/components/switcher.js")
UX_SUITE = Path("scripts/lookout_ux_suite.py")

VIEW_STATE_IDS = (
    "acoustic-mode-control",
    "events-panel-toggle",
    "display-ai-details-toggle",
    "display-clock-pin-toggle",
)
DENSITY_VALUES = ("auto", "1-col", "2-col", "3-col", "4-col")


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def _media_blocks(css: str, condition: str) -> list[str]:
    blocks: list[str] = []
    marker = f"@media ({condition})"
    pos = 0
    while True:
        idx = css.find(marker, pos)
        if idx == -1:
            break
        brace = css.find("{", idx)
        depth = 0
        end = brace
        for i in range(brace, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        blocks.append(css[brace + 1 : end])
        pos = end + 1
    return blocks


def test_display_trigger_and_panel_are_header_anchored_and_unique() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert html.count('id="display-menu"') == 1
    assert html.count('id="display-menu-btn"') == 1

    header_start = html.index('class="header-actions"')
    density_start = html.index('id="grid-density-controls"')
    trigger_start = html.index('id="display-menu-btn"')
    menu_start = html.index('id="display-menu"')
    scroll_start = html.index('id="control-bar-scroll"')
    focus_start = html.index('id="focus-stage"')
    badge_start = html.index('id="live-duration-badge"')

    # Both trigger and panel precede the focus-stage scroll row, so
    # switchToGrid() cannot hide them along with #focus-stage.
    assert header_start < density_start < trigger_start < menu_start < scroll_start
    assert menu_start < focus_start < badge_start

    trigger = html[trigger_start - 80 : menu_start]
    for attribute in (
        'aria-controls="display-menu"',
        'aria-expanded="false"',
        'aria-label="Display options"',
        'title="Display options"',
        "data-icon=",
        'class="btn btn-sm display-menu-btn"',
    ):
        assert attribute in trigger, attribute

    menu = html[menu_start:badge_start]
    assert 'id="display-menu-btn"' not in menu, "trigger must not be nested in the panel"
    assert 'role="dialog"' in menu and 'aria-label="Display options"' in menu
    assert "hidden" in menu.split(">")[0]


def test_view_state_controls_are_hosted_once_in_the_shared_popover() -> None:
    html = INDEX.read_text(encoding="utf-8")
    menu_start = html.index('id="display-menu"')
    menu_end = html.index('id="live-duration-badge"')
    panel = html[menu_start:menu_end]

    for control_id in VIEW_STATE_IDS:
        needle = f'id="{control_id}"'
        assert html.count(needle) == 1, f"{control_id} must not be duplicated"
        assert needle in panel, f"{control_id} missing from the shared popover"

    assert panel.count('class="display-menu-row"') == 4


def test_phone_header_has_no_inline_font_override_and_popover_is_reset() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    for control_id in ("view-grid-btn", "view-focus-btn"):
        start = html.index(f'id="{control_id}"')
        button = html[start : html.index(">", start)]
        assert "font-size:0.75rem" not in button, control_id
        assert "padding:2px 8px" not in button, control_id
        assert "data-icon=" in button and "aria-label=" in button and "title=" in button

    phone = "\n".join(_media_blocks(css, "max-width: 480px"))
    assert ".header-actions .btn" in phone
    assert "width: 32px" in phone
    assert "font-size: 0" in phone
    assert "content: attr(data-icon)" in phone
    # The popover lives in the header too; its labels must stay readable.
    assert ".header-actions .display-menu .btn" in phone
    assert "content: none" in phone
    assert "position: relative" in _rule(css, ".header-actions")
    assert "padding: var(--space-2xs) var(--space-xs)" in _rule(css, ".btn-sm")


def test_grid_density_is_not_hidden_on_phones() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    small_blocks = _media_blocks(css, "max-width: 640px")
    compact = [re.sub(r"\s+", "", block) for block in small_blocks]
    assert not any(
        "#grid-density-controls" in block and "display:none" in block for block in compact
    ), small_blocks

    visible = "\n".join(small_blocks)
    assert "#grid-density-controls" in visible
    assert "min-width: 0" in visible
    assert "#grid-density-select" in visible and "max-width: 100%" in visible

    assert html.count('id="grid-density-select"') == 1
    select = html[html.index('id="grid-density-select"') : html.index("</select>")]
    for value in DENSITY_VALUES:
        assert select.count(f'value="{value}"') == 1, value


def test_switcher_chips_are_non_shrinking_and_scroll_affordance_intact() -> None:
    css = CSS.read_text(encoding="utf-8")
    js = SWITCHER.read_text(encoding="utf-8")

    assert "flex-shrink: 0" in _rule(css, ".cam-tab")
    assert "scrollWidth > this.container.clientWidth + 2" in js
    assert "mask-image" in _rule(css, '.switcher-bar[data-scrollable="true"]')

    phone = "\n".join(_media_blocks(css, "max-width: 640px"))
    assert "-webkit-overflow-scrolling: touch" in phone
    assert ".switcher-bar::-webkit-scrollbar-thumb" in phone


def test_switch_to_grid_mirrors_persisted_view_state() -> None:
    app = APP.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^  switchToGrid\(\).*?(?=^  switchToFocus\()", app)
    assert match, "missing switchToGrid() implementation"
    body = match.group(0)

    assert 'this.setPlayerMode("live")' in body
    assert "this.fetchEvents()" in body
    # The shared popover must mirror persisted acoustic/events/AI/clock state.
    assert "this.applyFocusPreferences()" in body


def test_ux_suite_covers_grid_display_at_phone_widths() -> None:
    src = UX_SUITE.read_text(encoding="utf-8")

    assert "def phase_grid_display(" in src
    assert 'report["phases"]["grid_display"]' in src
    for check in (
        "grid_display_trigger_visible_narrow",
        "grid_display_opens_in_grid",
        "grid_density_selectable_narrow",
        "grid_density_class_applied",
        "grid_header_labels_not_clipped",
        "grid_switcher_last_chip_reachable",
    ):
        assert check in src, check
