"""TR-16: viewer assets keep Ingress-safe URL resolution."""

from pathlib import Path

STATIC = Path("src/lookout/adapters/web/static")
CHANGED_ASSETS = (
    STATIC / "index.html",
    STATIC / "css/tokens.css",
    STATIC / "css/viewer.css",
    STATIC / "js/app.js",
    STATIC / "js/components/settings-modal.js",
    STATIC / "js/components/scrubber.js",
    STATIC / "js/components/switcher.js",
)


def test_viewer_keeps_ingress_safe_url_resolution() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "js/app.js").read_text(encoding="utf-8")

    assert '<base href="/">' in html
    assert "resolveUrl(" in app
    assert 'resolveUrl("/api/events/stream")' in app

    for path in CHANGED_ASSETS:
        src = path.read_text(encoding="utf-8")
        assert "https://lookout.home.ajx.codes" not in src, str(path)
        # A hard-coded ingress path would appear as a quoted path literal;
        # example placeholder URLs in settings text are not API calls.
        assert '"/api/hassio_ingress/' not in src, str(path)
