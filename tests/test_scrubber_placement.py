from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")


def test_scrubber_under_cameras_not_events() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "scrubber-container" in src
    assert "<footer id=\"scrubber-container\"></footer>" not in src
    # ensure within stage section: scrubber appears before focus-stage/aside? basic check
    assert src.find("scrubber-container") < src.find("timeline-section")
