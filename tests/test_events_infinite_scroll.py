from pathlib import Path

TIMELINE = Path("src/lookout/adapters/web/static/js/components/timeline.js")


def test_timeline_infinite_scroll() -> None:
    src = TIMELINE.read_text(encoding="utf-8")
    assert any(k in src for k in ["loadMore", "IntersectionObserver", "scroll"])
    assert "pageSize" in src
