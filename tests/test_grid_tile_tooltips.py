from pathlib import Path

LIVE_GRID = Path("src/lookout/adapters/web/static/js/components/live-grid.js")


def test_grid_tile_controls_have_tooltips() -> None:
    src = LIVE_GRID.read_text(encoding="utf-8")
    assert "title=" in src
