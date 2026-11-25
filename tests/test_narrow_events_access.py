"""TR-13: narrow/tablet/foldable layouts expose a jump-to-events control."""

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


def test_narrow_layout_has_jump_to_events_control() -> None:
    css = CSS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert 'id="jump-to-events-btn"' in html
    assert "jump-to-events-btn" in app
    assert "scrollIntoView" in app

    assert "display: none" in _rule(css, "#jump-to-events-btn")
    narrow = _media_blocks(css, "max-width: 960px")
    assert any(
        "#jump-to-events-btn" in block and "display: inline-flex" in block for block in narrow
    ), narrow
