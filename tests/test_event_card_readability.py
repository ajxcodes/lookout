"""TR-8/TR-9: wrapped event summaries and single-line camera tags."""

import re
from pathlib import Path

CSS = Path("src/lookout/adapters/web/static/css/viewer.css")
TIMELINE = Path("src/lookout/adapters/web/static/js/components/timeline.js")


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_event_summary_wraps_instead_of_ellipsis() -> None:
    css = CSS.read_text(encoding="utf-8")
    summary = _rule(css, ".event-summary")
    assert "white-space: normal" in summary
    assert "overflow-wrap: anywhere" in summary
    assert "text-overflow: ellipsis" not in summary
    assert "white-space: nowrap" not in summary
    assert 'title="${evt.summary || ""}"' in TIMELINE.read_text(encoding="utf-8")


def test_event_camera_tag_stays_on_one_line() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "white-space: nowrap" in _rule(css, ".event-camera-tag")
    assert "flex-wrap: wrap" in _rule(css, ".event-header-row")
