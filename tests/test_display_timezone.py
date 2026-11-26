"""Source-level and endpoint guards for the viewer display timezone.

The config value is non-secret, exposed only through a read-only public
endpoint, and consumed by the viewer's pure timezone helper so event and
snapshot timestamps can be shown in a fixed zone plus browser-local time.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "src/lookout/config/schema.py"
EXAMPLE = ROOT / "config.example.yaml"
APP = ROOT / "src/lookout/adapters/web/app.py"
TZ_JS = ROOT / "src/lookout/adapters/web/static/js/utils/timezone.js"
TIMELINE_JS = ROOT / "src/lookout/adapters/web/static/js/components/timeline.js"
EVENT_PLAYER_JS = ROOT / "src/lookout/adapters/web/static/js/components/event-player.js"
APP_JS = ROOT / "src/lookout/adapters/web/static/js/app.js"


def test_config_schema_exposes_optional_display_timezone() -> None:
    cfg = LookoutConfig()
    assert cfg.display_timezone == ""
    assert LookoutConfig(display_timezone="America/New_York").display_timezone == (
        "America/New_York"
    )
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "display_timezone: str = Field(" in schema
    assert "empty means browser-local time" in schema
    assert "display_timezone:" in EXAMPLE.read_text(encoding="utf-8")


def test_public_config_endpoint_is_read_only_and_secret_free() -> None:
    app = create_app(
        config=LookoutConfig(
            display_timezone="America/New_York",
            system_name="Private Name",
            home_assistant={"enabled": True, "access_token": "super-secret"},
        )
    )
    client = TestClient(app)
    response = client.get("/api/config/public")
    assert response.status_code == 200
    body = response.json()
    assert body == {"display_timezone": "America/New_York"}
    assert "super-secret" not in response.text
    assert "Private Name" not in response.text
    assert client.post("/api/config/public", json={}).status_code in (405, 404)


def test_viewer_helper_uses_intl_with_configured_zone_and_local_hint() -> None:
    helper = TZ_JS.read_text(encoding="utf-8")
    assert "Intl.DateTimeFormat(undefined, withZone(options))" in helper
    assert "timeZone: configuredTimeZone" in helper
    assert "Browser local time:" in helper
    assert "export function setDisplayTimezone" in helper
    assert "export function zonedDayKey" in helper
    assert "export function zonedHour" in helper

    app = APP_JS.read_text(encoding="utf-8")
    assert 'fetch(resolveUrl("/api/config/public"))' in app
    assert "setDisplayTimezone(" in app
    assert "loadPublicConfig" in app


def test_timeline_and_snapshot_metadata_use_helper_consistently() -> None:
    timeline = TIMELINE_JS.read_text(encoding="utf-8")
    assert 'formatInDisplayZone(date, { month: "short", day: "numeric" })' in timeline
    assert "zonedDayKey(date) === zonedDayKey(now)" in timeline
    assert "zonedHour(date)" in timeline
    assert "eventTimeWithLocalHint(evt.timestamp)" in timeline
    assert 'class="event-time"${timeTitleAttr}' in timeline

    player = EVENT_PLAYER_JS.read_text(encoding="utf-8")
    assert "eventTimeWithLocalHint(eventItem.timestamp" in player
    assert 'class="meta-time-local"' in player
    assert "formatInDisplayZone(new Date(this.currentEvent.timestamp)" in player
