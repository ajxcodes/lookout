from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")


def test_reset_section_lists_selective_options() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for phrase in [
        "Reset camera configuration (remove all cameras, keep provider auth)",
        "Reset provider authentication (logout all providers)",
        "Reset notification settings (restore defaults)",
        "Reset storage settings (restore defaults)",
        "Clear all event history",
        "Clear all media files",
    ]:
        assert phrase in src, phrase
