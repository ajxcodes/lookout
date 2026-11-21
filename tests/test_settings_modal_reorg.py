from pathlib import Path

INDEX = Path("src/lookout/adapters/web/static/index.html")


def test_settings_modal_has_clear_sections() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for label in ["AI Runtime", "Entity Catalog", "Face Gallery", "Home Assistant", "Integrations", "Camera Notifications", "Storage", "Hardware", "Reset"]:
        assert label in src, label
