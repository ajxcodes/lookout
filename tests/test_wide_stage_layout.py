"""TR-11/TR-12: fluid resizable events panel and capped Auto-Fit grid."""

import re
from pathlib import Path

CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
HTML = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


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


def test_events_panel_width_is_fluid_and_resizable() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    main = _rule(css, ".viewer-main")
    assert "var(--events-panel-width, clamp(" in main
    assert "380px" not in main

    assert 'id="events-resize-handle"' in html
    assert 'role="separator"' in html

    assert "--events-panel-width" in app
    assert "Math.max(280" in app
    assert "Math.min(900" in app


def test_auto_fit_columns_capped_and_rows_stretch() -> None:
    css = CSS.read_text(encoding="utf-8")

    auto = _rule(css, ".live-grid-container.density-auto")
    assert "auto-fit" not in auto
    assert "grid-auto-rows" in auto

    wide = _media_blocks(css, "min-width: 1440px")
    assert any(
        ".live-grid-container.density-auto" in block and "repeat(3, minmax(0, 1fr))" in block
        for block in wide
    ), wide

    roomy = _media_blocks(css, "min-width: 961px")
    assert any(".density-auto .tile-stage" in block and "flex: 1" in block for block in roomy), (
        roomy
    )
