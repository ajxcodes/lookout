"""TR-14: chip strip exposes a visible scroll affordance when it overflows."""

import re
from pathlib import Path

SWITCHER = Path("src/lookout/adapters/web/static/js/components/switcher.js")
CSS = Path("src/lookout/adapters/web/static/css/viewer.css")


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_chip_strip_exposes_scroll_affordance() -> None:
    js = SWITCHER.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "dataset.scrollable" in js
    assert "scrollWidth > this.container.clientWidth + 2" in js

    rule = _rule(css, '.switcher-bar[data-scrollable="true"]')
    assert "mask-image" in rule
    assert "linear-gradient" in rule
