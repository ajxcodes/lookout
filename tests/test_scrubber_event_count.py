"""TR-10: scrubber distinguishes loaded events from the server total."""

from pathlib import Path

SCRUBBER = Path("src/lookout/adapters/web/static/js/components/scrubber.js")
APP = Path("src/lookout/adapters/web/static/js/app.js")


def test_scrubber_label_distinguishes_loaded_from_total() -> None:
    scrubber = SCRUBBER.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert "`${loaded} of ${total.toLocaleString()} events`" in scrubber
    assert "toLocaleString()" in scrubber
    assert "setEvents(events, opts = {})" in scrubber

    assert "data.total" in app
    assert "eventsTotal" in app
    assert "setEvents(this.events, { total: this.eventsTotal })" in app
