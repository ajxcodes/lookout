from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_events_panel_is_collapsible() -> None:
    src = INDEX.read_text(encoding="utf-8") + APP.read_text(encoding="utf-8")
    assert "events-panel-toggle" in src or "timeline-collapse" in src or "collapse-events" in src
