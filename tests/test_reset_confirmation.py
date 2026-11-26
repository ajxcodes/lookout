from pathlib import Path

SM = Path("src/lookout/adapters/web/static/js/components/settings-modal.js")


def test_reset_requires_typed_confirmation_and_second_dialog() -> None:
    src = SM.read_text(encoding="utf-8")
    assert "RESET" in src
    assert "confirm" in src.lower()
    # second dialog
    assert "window.confirm" in src
