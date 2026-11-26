"""TR-1: header wraps on narrow screens and the density picker stays reachable."""

import re
from pathlib import Path

CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
HTML = Path("src/lookout/adapters/web/static/index.html")


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


def test_header_wraps_and_density_hidden_on_small_screens() -> None:
    """The density control used to be hidden at <=640px; it is now reachable."""
    css = CSS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")

    assert "flex-wrap: wrap" in _rule(css, ".app-header")

    header_actions = _rule(css, ".header-actions")
    assert "flex-wrap: wrap" in header_actions
    assert "min-width: 0" in header_actions

    small_blocks = _media_blocks(css, "max-width: 640px")
    assert not any(
        "#grid-density-controls" in block and "display: none" in block for block in small_blocks
    ), small_blocks

    assert 'id="grid-density-select"' in html
    select = html[html.index('id="grid-density-select"') : html.index("</select>")]
    for value in ("auto", "1-col", "2-col", "3-col", "4-col"):
        assert f'value="{value}"' in select

    assert 'id="open-settings-btn"' in html
    assert 'id="add-camera-dropdown-btn"' in html


def test_phone_header_actions_are_icon_only_to_keep_one_row() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    phone_blocks = _media_blocks(css, "max-width: 480px")
    assert phone_blocks, "missing phone-width icon-only header block"
    block = "\n".join(phone_blocks)
    assert ".header-actions .btn" in block
    assert "width: 32px" in block
    assert "font-size: 0" in block
    assert "content: attr(data-icon)" in block

    for control_id in (
        "jump-to-events-btn",
        "view-grid-btn",
        "view-focus-btn",
        "global-pause-btn",
        "open-settings-btn",
        "add-camera-dropdown-btn",
    ):
        needle = f'id="{control_id}"'
        button = html[html.index(needle) - 60 : html.index(needle) + 220]
        assert "data-icon=" in button, control_id
        assert "aria-label=" in button, control_id
