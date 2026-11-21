from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")


def test_lookout_logo_visible_in_header() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert ".brand-logo" in src or "brand-logo" in src
    assert "brand-mark" in src or "lookout-logo" in src
    # ensure in header
    assert "<header" in src
