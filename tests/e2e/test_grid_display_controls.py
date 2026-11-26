"""TR-15: the Grid display control and density picker work at phone widths."""

from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

GRID_VIEWPORTS = ((320, 720), (412, 839))


@pytest.mark.parametrize(("width", "height"), GRID_VIEWPORTS)
def test_grid_display_control_and_density_are_reachable_at_phone_widths(
    page: Page, standalone_base_url: str, width: int, height: int
) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(standalone_base_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_function("() => !!window.lookoutApp", timeout=15000)
    page.wait_for_timeout(300)

    if page.evaluate("() => window.lookoutApp.viewMode") != "grid":
        page.locator("#view-grid-btn").click()
        page.wait_for_timeout(200)

    trigger = page.locator("#display-menu-btn")
    expect(trigger).to_be_visible()
    expect(page.locator("#grid-density-select")).to_be_visible()
    assert page.evaluate("() => document.querySelector('#display-menu').hidden") is True

    trigger.click()
    menu = page.locator("#display-menu:not([hidden])")
    expect(menu).to_be_visible()
    assert trigger.get_attribute("aria-expanded") == "true"

    box = menu.bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1

    # Header buttons keep readable icon-only labels with intact accessible names.
    probe: dict[str, Any] = page.evaluate(
        """() => {
            const buttons = [...document.querySelectorAll('.header-actions .btn')];
            return {
                clipped: buttons.filter(b => b.scrollWidth > b.clientWidth + 1).map(b => b.id),
                names: buttons.every(b => b.getAttribute('aria-label') && b.getAttribute('title')),
                overflowX: document.documentElement.scrollWidth -
                    document.documentElement.clientWidth,
            };
        }"""
    )
    assert probe["clipped"] == []
    assert probe["names"] is True
    assert probe["overflowX"] == 0

    page.select_option("#grid-density-select", "2-col")
    class_name = page.locator("#live-grid-container").get_attribute("class") or ""
    assert "density-2-col" in class_name
    assert page.evaluate("() => localStorage.getItem('lookout_grid_density')") == "2-col"

    # The last camera chip is reachable by scrolling the overflowing strip.
    last_chip_reachable = page.evaluate(
        """() => {
            const strip = document.querySelector('#switcher-container');
            strip.scrollLeft = strip.scrollWidth;
            const last = strip.querySelector('.cam-tab:last-child');
            return !!last &&
                last.getBoundingClientRect().right <= strip.getBoundingClientRect().right + 1;
        }"""
    )
    assert last_chip_reachable is True
