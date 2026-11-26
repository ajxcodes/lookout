"""TR-5/TR-6/TR-7: Settings modal focus lifecycle, trap, and restoration."""

import re
from pathlib import Path

MODAL = Path("src/lookout/adapters/web/static/js/components/settings-modal.js")


def _method(src: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  (?:async )?{re.escape(name)}\([^)]*\)\s*\{{.*?(?=^  \}})",
        src,
    )
    assert match, f"missing method: {name}"
    return match.group(0)


def test_settings_modal_moves_focus_into_dialog_on_open() -> None:
    src = MODAL.read_text(encoding="utf-8")
    body = _method(src, "open")
    assert "this._lastFocused = document.activeElement" in body
    assert 'this.modal.classList.add("open")' in body
    assert "_focusFirst()" in body
    assert body.index("_focusFirst()") > body.index('classList.add("open")')
    first_await = body.find("await ")
    assert first_await != -1
    assert body.index("_focusFirst()") < first_await


def test_settings_modal_traps_tab_and_closes_on_escape() -> None:
    src = MODAL.read_text(encoding="utf-8")
    handler = _method(src, "_handleKeydown")
    assert 'event.key === "Escape"' in handler
    assert "close()" in handler
    assert 'event.key === "Tab"' in handler
    assert "_trapFocus(event)" in handler
    assert 'addEventListener("keydown"' in src

    trap = _method(src, "_trapFocus")
    assert re.search(r"const first = focusables\[0\]", trap)
    assert re.search(r"const last = focusables\[focusables\.length - 1\]", trap)
    assert "event.shiftKey" in trap


def test_settings_modal_restores_focus_on_close() -> None:
    src = MODAL.read_text(encoding="utf-8")
    close_body = _method(src, "close")
    assert "this._lastFocused" in close_body
    assert "isConnected" in close_body
    assert ".focus()" in close_body
    assert '"#modal-close-btn"' in src
    assert "() => this.close()" in src
