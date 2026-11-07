"""Playwright E2E browser tests for simulated Home Assistant Ingress environment."""

from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_ingress_spa_routing_and_base_href(page: Page, ingress_base_url: str) -> None:
    """TR-12: Verify injected base href tag points to Ingress reverse proxy prefix."""
    page.goto(ingress_base_url)
    expect(page.locator(".brand-logo")).to_be_visible()

    base_href = page.locator("base").get_attribute("href")
    assert base_href == "/api/hassio_ingress/test-token/"
    assert page.locator("base").get_attribute("href") == "/api/hassio_ingress/test-token/"


def test_ingress_static_assets_status_ok(page: Page, ingress_base_url: str) -> None:
    """TR-13: Verify stylesheets and scripts load under Ingress prefix with HTTP 200."""
    asset_responses: list[Any] = []
    page.on(
        "response",
        lambda response: asset_responses.append(response) if "static/" in response.url else None,
    )

    page.goto(ingress_base_url)
    expect(page.locator(".brand-logo")).to_be_visible()

    assert len(asset_responses) > 0
    assert all(r.status == 200 for r in asset_responses)


def test_ingress_api_calls_within_prefix(page: Page, ingress_base_url: str) -> None:
    """TR-14: Verify all frontend API fetch requests resolve within the Ingress path prefix."""
    api_request_urls: list[str] = []
    page.on(
        "request",
        lambda request: (
            api_request_urls.append(request.url)
            if request.resource_type in ("fetch", "eventsource")
            else None
        ),
    )

    page.goto(ingress_base_url)
    expect(page.locator(".brand-logo")).to_be_visible()

    assert len(api_request_urls) > 0
    assert all("/api/hassio_ingress/test-token/api/" in url for url in api_request_urls)


def test_ingress_sse_stream_receives_live_event(
    page: Page, configured_server: dict[str, Any], ingress_base_url: str
) -> None:
    """TR-15: Verify real-time SSE stream receives live event broadcasts under Ingress prefix."""
    page.goto(ingress_base_url)
    expect(page.locator(".brand-logo")).to_be_visible()
    expect(page.locator(".event-card").first).to_be_visible()

    # Trigger live event through ingress or backend trigger API
    standalone_base_url = configured_server["standalone_base_url"]
    with httpx.Client() as client:
        res = client.post(
            f"{standalone_base_url}/api/v1/events/trigger",
            json={
                "camera_id": "front-porch",
                "summary": "Ingress live SSE event stream test",
            },
        )
        assert res.status_code == 200
        live_event_id = res.json()["event_id"]

    event_locator = page.locator(f'[data-event-id="{live_event_id}"]')
    expect(event_locator).to_be_visible(timeout=8000)
    assert page.locator(f'[data-event-id="{live_event_id}"]').is_visible()


def test_ingress_setup_wizard_redirect_within_ingress_prefix(
    page: Page, unconfigured_server: dict[str, Any]
) -> None:
    """Verify unconfigured app under Ingress prefix redirects to {ingress_path}/setup."""
    ingress_url = unconfigured_server["ingress_base_url"]
    page.goto(ingress_url)

    expect(page.locator("#step-1")).to_be_visible()
    assert "/api/hassio_ingress/test-token/setup" in page.url


def test_ingress_iframe_embedding_and_interaction(page: Page, ingress_base_url: str) -> None:
    """TR-16: Verify Lookout viewer operates cleanly when embedded in Home Assistant iframe."""
    iframe_html = (
        '<!DOCTYPE html><html lang="en">'
        "<head><title>Home Assistant Ingress Host</title></head>"
        '<body style="margin:0;padding:0;">'
        f'<iframe id="ingress-iframe" src="{ingress_base_url}" width="100%" height="800"></iframe>'
        "</body></html>"
    )
    page.set_content(iframe_html)

    frame = page.frame_locator("#ingress-iframe")
    expect(frame.locator(".brand-logo")).to_be_visible()
    expect(frame.locator(".camera-chip").first).to_be_visible()

    assert frame.locator(".brand-logo").is_visible() and frame.locator(".camera-chip").count() >= 1
