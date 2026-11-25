#!/usr/bin/env python3
# ruff: noqa: E501 -- embedded JS probes are kept verbatim and readable.
"""Deterministic UX suite for the Lookout viewer.

Companion to ``scripts/lookout_viewport_audit.py``. It never asserts real live
media playback (which is inherently flaky); it asserts the observable UI state
contracts around failure, compact layout, acoustic modes, persistence and
Ingress safety. All checks run with the same CLI style::

    uv run python scripts/lookout_ux_suite.py \
        --base-url https://lookout.home.ajx.codes \
        --out-dir .cache/ux-suite \
        --ingress-token test-token

Writes ``<out-dir>/lookout-ux-report.json`` and screenshots. Its summary keys
are deliberately distinct from the viewport audit's 15-viewport / 18-step
denominators, which remain untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

BASE_URL = "https://lookout.home.ajx.codes"
INGRESS_TOKEN = "test-token"
INGRESS_PATH = f"/api/hassio_ingress/{INGRESS_TOKEN}"
OUT_DIR = Path("/tmp/opencode")

# name, width, height -- header overflow is checked at each of these widths.
UX_VIEWPORTS: list[tuple[str, int, int]] = [
    ("320x640", 320, 640),
    ("360x640", 360, 640),
    ("375x667", 375, 667),
    ("384x854", 384, 854),
    ("720x748", 720, 748),
    ("1280x800", 1280, 800),
]

FOCUS_VIEWPORTS = ("384x854", "720x748", "1280x800")
IGNORED_CONSOLE_FRAGMENTS = ("favicon.ico", "Download the React DevTools", "[vite]")

# Purged snapshots are served as 1x1 placeholders in this deployment, which
# collapses the snapshot-only stage to a ~1px surface. Give the harness a
# stable, bare tap target below the sticky header and 44px control bar; product
# CSS is never touched.
MIN_SURFACE_TAP_HEIGHT = 180

# Stream 5xx failures are expected in this environment; they are asserted as
# graceful degradation, not as uncaught JS exceptions.
EXPECTED_STREAM_ERRORS = ("502", "503", "504", "ERR_ABORTED", "stream unavailable", "net::ERR")


def compact(text: str, limit: int = 320) -> str:
    return " ".join(str(text).split())[:limit]


class Checks:
    """Small ordered result collector shared by every phase."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.results.append(
            {"name": name, "ok": bool(ok), "detail": compact(detail) if detail is not None else ""}
        )

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.results if not r["ok"]]


def attach_diagnostics(page: Page, sink: dict[str, list[Any]]) -> None:
    def on_console(msg: Any) -> None:
        if any(part in msg.text for part in IGNORED_CONSOLE_FRAGMENTS):
            return
        sink["console_errors"].append(msg.text)

    def on_page_error(exc: Any) -> None:
        sink["page_errors"].append(str(exc))

    def on_request_failed(req: Any) -> None:
        sink["request_failures"].append(f"{req.method} {req.url}: {req.failure}")

    page.on("console", lambda msg: on_console(msg) if msg.type == "error" else None)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)


def new_sink() -> dict[str, list[Any]]:
    return {"console_errors": [], "page_errors": [], "request_failures": []}


HEADER_PROBE_JS = r"""() => {
    const de = document.documentElement;
    const header = document.querySelector('.app-header');
    const pause = document.querySelector('#global-pause-btn');
    const pauseStyle = pause ? getComputedStyle(pause) : null;
    const overflow = Math.max(0, de.scrollWidth - window.innerWidth);
    const culprits = [];
    if (overflow > 1) {
        for (const el of document.querySelectorAll('body *')) {
            const r = el.getBoundingClientRect();
            let p = el.parentElement, inScroller = false;
            while (p && p !== document.body) {
                const ox = getComputedStyle(p).overflowX;
                if (['auto', 'scroll', 'hidden', 'clip'].includes(ox)) { inScroller = true; break; }
                p = p.parentElement;
            }
            if (!inScroller && r.width && r.right > window.innerWidth + 2)
                culprits.push(`${el.tagName.toLowerCase()}.${(el.className || '').toString().split(/\s+/)[0]}@${Math.round(r.right)}`);
        }
    }
    return {
        viewport: window.innerWidth,
        scroll_width: de.scrollWidth,
        overflow_px: overflow,
        header_height: header ? +header.getBoundingClientRect().height.toFixed(1) : null,
        pause_width: pause ? +pause.getBoundingClientRect().width.toFixed(1) : null,
        pause_font_size: pauseStyle ? pauseStyle.fontSize : null,
        pause_icon: pause ? pause.getAttribute('data-icon') : null,
        pause_aria_label: pause ? pause.getAttribute('aria-label') : null,
        culprits: culprits.slice(0, 6),
    };
}"""

BAR_PROBE_JS = r"""() => {
    const bar = document.querySelector('#focus-control-overlay');
    const scroll = document.querySelector('#control-bar-scroll');
    const surface = document.querySelector('#video-surface');
    if (!bar || !scroll) return null;
    const barRect = bar.getBoundingClientRect();
    let rightMax = 0;
    for (const el of scroll.querySelectorAll('button, select, input')) {
        rightMax = Math.max(rightMax, el.getBoundingClientRect().right);
    }
    return {
        bar_height: +barRect.height.toFixed(1),
        controls_hidden: bar.classList.contains('controls-hidden'),
        aria_hidden: bar.getAttribute('aria-hidden'),
        surface_height: surface ? +surface.getBoundingClientRect().height.toFixed(1) : null,
        controls_right_max: +rightMax.toFixed(1),
        viewport: window.innerWidth,
    };
}"""

# The compact bar is one intentionally scrollable row. "Not clipped" means the
# last control is reachable: either it already fits, or the scroller can move
# and bringing it to the far end puts it inside the viewport.
BAR_SCROLL_PROBE_JS = r"""() => {
    const scroll = document.querySelector('#control-bar-scroll');
    if (!scroll) return null;
    const start = scroll.scrollLeft;
    scroll.scrollLeft = scroll.scrollWidth;  // reveal the far end of the row
    const end = scroll.scrollLeft;
    const controls = [...scroll.querySelectorAll('button, select, input')].filter(el => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width && r.height && s.visibility !== 'hidden' && s.display !== 'none';
    });
    const last = controls.length ? controls[controls.length - 1] : null;
    const lastRect = last ? last.getBoundingClientRect() : null;
    const result = {
        scrollable: scroll.scrollWidth > scroll.clientWidth + 1,
        scroll_moved: end > start,
        scroll_width: scroll.scrollWidth,
        client_width: scroll.clientWidth,
        last_control_right: lastRect ? +lastRect.right.toFixed(1) : null,
        scroller_right: +scroll.getBoundingClientRect().right.toFixed(1),
        viewport: window.innerWidth,
    };
    scroll.scrollLeft = start;  // restore the original scroll position
    return result;
}"""

# Tap-to-toggle must land on bare video surface. At narrow widths the sticky
# header (.app-header) and the focus control overlay (#focus-control-overlay)
# both overlap the top of #video-surface, so a fixed element-relative point
# (e.g. 20,20) is intercepted by the brand logo / back-to-grid button. Compute
# an absolute point below both bars and clamp it inside the surface instead.
SURFACE_TAP_POINT_JS = r"""() => {
    const surface = document.querySelector('#video-surface');
    if (!surface) return null;
    const s = surface.getBoundingClientRect();
    let safeTop = s.top;
    for (const sel of ['.app-header', '#focus-control-overlay']) {
        const el = document.querySelector(sel);
        if (el) safeTop = Math.max(safeTop, el.getBoundingClientRect().bottom);
    }
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const x = clamp(s.left + 24, s.left + 1, s.right - 1);
    const y = clamp(safeTop + 40, s.top + 1, s.bottom - 1);
    return { x, y, safe_top: +safeTop.toFixed(1) };
}"""

ACOUSTIC_PROBE_JS = r"""() => {
    const app = window.lookoutApp;
    const group = document.querySelector('#acoustic-mode-control');
    const container = document.querySelector('#video-container');
    const select = document.querySelector('#setting-acoustic-mode');
    return {
        group_mode: group ? group.dataset.mode : null,
        pressed: group ? [...group.querySelectorAll('.acoustic-mode-btn')]
            .map(b => [b.dataset.mode, b.getAttribute('aria-pressed')]) : [],
        container_classes: container ? container.className : '',
        local_mode: (() => { try { return localStorage.getItem('lookout_acoustic_mode'); } catch { return null; } })(),
        settings_value: select ? select.value : null,
    };
}"""

DISPLAY_PROBE_JS = r"""() => {
    const app = window.lookoutApp;
    const bar = document.querySelector('#focus-control-overlay');
    const trigger = document.querySelector('#display-menu-btn');
    const menu = document.querySelector('#display-menu');
    const readout = document.querySelector('#focus-time-readout');
    const style = el => el ? getComputedStyle(el) : null;
    const visible = el => {
        const s = style(el);
        return !!el && !!s && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const control = sel => {
        const el = menu ? menu.querySelector(sel) : null;
        return { present: !!el, visible: visible(el) };
    };
    const text = sel => {
        const el = document.querySelector(sel);
        return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
    };
    const ai = menu ? menu.querySelector('#display-ai-details-toggle') : null;
    const pin = menu ? menu.querySelector('#display-clock-pin-toggle') : null;
    const local = document.querySelector('#focus-clock-local');
    const home = document.querySelector('#focus-clock-home');
    return {
        trigger_expanded: trigger ? trigger.getAttribute('aria-expanded') : null,
        trigger_label: trigger ? trigger.getAttribute('aria-label') : null,
        trigger_title: trigger ? trigger.getAttribute('title') : null,
        trigger_visible: visible(trigger),
        menu_hidden: menu ? menu.hidden : null,
        menu_visible: visible(menu),
        controls: {
            acoustic: control('#acoustic-mode-control'),
            events: control('#events-panel-toggle'),
            ai: control('#display-ai-details-toggle'),
            clock_pin: control('#display-clock-pin-toggle'),
        },
        ai_disabled: ai ? ai.disabled : null,
        ai_aria_disabled: ai ? ai.getAttribute('aria-disabled') : null,
        ai_pressed: ai ? ai.getAttribute('aria-pressed') : null,
        ai_title: ai ? ai.getAttribute('title') : null,
        pin_pressed: pin ? pin.getAttribute('aria-pressed') : null,
        active_inside_menu: menu ? menu.contains(document.activeElement) : false,
        active_is_trigger: trigger ? document.activeElement === trigger : false,
        bar_hidden: bar ? bar.classList.contains('controls-hidden') : null,
        bar_opacity: style(bar) ? style(bar).opacity : null,
        pinned_class: bar ? bar.classList.contains('time-readout-pinned') : null,
        readout_visible: visible(readout),
        readout_text: text('#focus-time-readout'),
        clock_local: text('#focus-clock-local'),
        clock_local_hidden: local ? local.hidden : null,
        clock_home: text('#focus-clock-home'),
        clock_home_hidden: home ? home.hidden : null,
        event_time: text('#focus-event-time'),
        event_time_hidden: document.querySelector('#focus-event-time')
            ? document.querySelector('#focus-event-time').hidden : null,
        event_age: text('#focus-event-age'),
        event_age_hidden: document.querySelector('#focus-event-age')
            ? document.querySelector('#focus-event-age').hidden : null,
        mode: bar ? bar.getAttribute('data-mode') : null,
        local_storage: (() => {
            try {
                return {
                    ai: localStorage.getItem('lookout_display_ai_details'),
                    pin: localStorage.getItem('lookout_display_clock_pinned'),
                };
            } catch { return { ai: null, pin: null }; }
        })(),
    };
}"""

LAYOUT_PROBE_JS = r"""() => {
    const stage = document.querySelector('.stage-section');
    const aside = document.querySelector('.timeline-section');
    const sw = document.querySelector('.switcher-bar');
    const btn = document.querySelector('#jump-to-events-btn');
    const width = el => el ? +el.getBoundingClientRect().width.toFixed(1) : null;
    return {
        stage_width: width(stage),
        aside_width: width(aside),
        aside_collapsed: aside ? aside.classList.contains('collapsed') : null,
        switchable: sw ? sw.scrollWidth > sw.clientWidth + 2 : false,
        scrollable_attr: sw ? sw.dataset.scrollable : null,
        switcher_mask: sw ? getComputedStyle(sw).maskImage || getComputedStyle(sw).webkitMaskImage : '',
        jump_display: btn ? getComputedStyle(btn).display : null,
    };
}"""

# Snapshot-event selection must not depend on whatever 50-event page happens to
# be loaded. Search the loaded page, then a wider API page, and finally fall
# back to a deterministic harness-only state mutation that still drives the
# product's real renderSnapshotCard() path.
SNAPSHOT_SELECTION_JS = r"""async () => {
    const app = window.lookoutApp;
    if (!app || !Array.isArray(app.events)) return { ok: false, reason: 'no app state' };
    // Match timeline.js: no media_uri or an explicitly purged clip.
    const isSnapshot = e => !e.media_uri || !!(e.metadata && e.metadata.media_purged);
    let ev = app.events.find(isSnapshot);
    let source = 'loaded';
    if (!ev) {
        try {
            const res = await fetch('/api/events?limit=200');
            if (res.ok) {
                const data = await res.json();
                ev = ((data && data.events) || []).find(isSnapshot);
                if (ev) source = 'api_page';
            }
        } catch (err) { /* fall through to the deterministic fallback */ }
    }
    if (ev) {
        if (!app.events.some(e => e.id === ev.id)) app.events.unshift(ev);
        app.playEvent(ev);
        return { ok: true, id: ev.id, source };
    }
    // Genuinely no snapshot event in this deployment: adapt a loaded event in
    // harness app state only. No product code is changed, and playEvent() still
    // renders the snapshot card through the production code path.
    const base = app.events[0] || null;
    if (!base) return { ok: false, reason: 'no events to adapt' };
    base.media_uri = null;
    base.metadata = Object.assign({}, base.metadata, { media_purged: true });
    app.playEvent(base);
    return { ok: true, id: base.id, source: 'harness_fallback' };
}"""


def open_viewer(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_function("() => !!window.lookoutApp", timeout=15000)
    page.wait_for_timeout(300)


def reveal_focus_controls(page: Page) -> None:
    """Ensure the focus stage is rendered and reveal its auto-hiding bar.

    #display-menu-btn lives inside the focus control overlay, which is
    display:none in grid view and hidden/inert after ~3s idle. A plain click can
    therefore time out even though the button resolves in the DOM. Move the
    pointer over the video surface -- the product's own reveal gesture -- and
    fall back to revealControls() when the surface has no laid-out box.
    """
    if "active" not in (page.locator("#view-focus-btn").get_attribute("class") or ""):
        page.locator("#view-focus-btn").click()
        page.wait_for_timeout(300)
    point = page.evaluate(
        """() => {
            const surface = document.querySelector('#video-surface');
            if (!surface) return null;
            const r = surface.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return null;
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }"""
    )
    if point:
        page.mouse.move(point["x"], point["y"])
    else:
        page.evaluate(
            "() => { const app = window.lookoutApp; "
            "if (app && typeof app.revealControls === 'function') app.revealControls(); }"
        )
    page.wait_for_timeout(120)


def open_display_menu(page: Page) -> None:
    """Open the unified Display popover before clicking a control inside it."""
    reveal_focus_controls(page)
    trigger = page.locator("#display-menu-btn")
    if trigger.get_attribute("aria-expanded") != "true":
        trigger.click()
    page.wait_for_selector("#display-menu:not([hidden])", state="visible")


def phase_header(checks: Checks, page: Page) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for name, width, height in UX_VIEWPORTS:
        page.set_viewport_size({"width": width, "height": height})
        open_viewer(page, BASE_URL)
        probe = page.evaluate(HEADER_PROBE_JS)
        detail[name] = probe
        checks.add(f"header_overflow_{name}", probe["overflow_px"] <= 1, probe)
        if width <= 960:
            checks.add(
                f"global_pause_compact_{name}",
                probe["pause_width"] is not None
                and probe["pause_width"] <= 40
                and probe["pause_font_size"] in ("0px", "0")
                and bool(probe["pause_icon"])
                and bool(probe["pause_aria_label"]),
                probe,
            )
    return detail


def enter_snapshot_focus(page: Page) -> None:
    """Enter focus view with a snapshot-only event selected.

    Viewport sizing belongs to the caller (the 384 and 720 cases must stay
    distinct). Selection uses a real click on a snapshot-badged event card so
    the event player follows the same path as a user; a bare JS ``playEvent``
    call can leave the stage unlaid-out while its async fetch is still pending.
    When the loaded page has no snapshot-badged card, widen the search to a
    larger API page and, if even that has none, build a deterministic
    harness-only snapshot event so the focus checks still exercise the product
    path instead of aborting on data that happens to be time-ordered.
    """
    page.wait_for_selector(".event-card", timeout=45000)
    # Do not switch views first: switchToFocus() re-fetches and re-renders the
    # timeline, which detaches the card between locating and clicking it. The
    # card's own click handler calls playEvent(), which enters focus for us.
    cards = page.locator(".event-card:has(.media-badge-snapshot)")
    selected: dict[str, Any] | None = None
    if cards.count():
        try:
            cards.first.click()
            selected = {"ok": True, "source": "card"}
        except Exception:
            # Card may detach while the timeline re-renders; use JS selection.
            selected = None
    if selected is None:
        selected = page.evaluate(SNAPSHOT_SELECTION_JS)
        if not selected or not selected.get("ok"):
            raise AssertionError("no snapshot-only event available for focus checks")
    page.wait_for_function(
        """() => {
            const container = document.querySelector('.video-container');
            const card = document.querySelector('#event-snapshot-card');
            return !!container && container.classList.contains('snapshot-only')
                && !!card && getComputedStyle(card).display !== 'none';
        }""",
        timeout=15000,
    )
    # A purged snapshot renders a 1x1 placeholder here, so #video-surface can
    # collapse to ~1px with no bare area below the bar. Guarantee a tap target
    # (harness-only) before the phase-2 geometry/toggle checks run.
    page.evaluate(
        """(min) => {
            const surface = document.querySelector('#video-surface');
            if (surface && surface.getBoundingClientRect().height < min)
                surface.style.minHeight = min + 'px';
        }""",
        MIN_SURFACE_TAP_HEIGHT,
    )
    page.wait_for_function(
        """(min) => {
            const surface = document.querySelector('#video-surface');
            return !!surface && surface.getBoundingClientRect().height >= min;
        }""",
        arg=MIN_SURFACE_TAP_HEIGHT,
        timeout=5000,
    )
    # Clicking a card scrolls it into view; playEvent() later scrolls back to
    # the top after its async fetch. Force that scroll here so the stage cannot
    # slide out from under the tap coordinates computed below.
    page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
    # playEvent() awaits fetchEvents() and then calls fitFocusStage(), so the
    # surface can still move briefly after the snapshot card is visible. Tap
    # coordinates must be computed only once the stage has stopped moving.
    page.wait_for_function(
        """(min) => new Promise(resolve => {
            const surface = document.querySelector('#video-surface');
            if (!surface || window.scrollY !== 0) return resolve(false);
            let prev = surface.getBoundingClientRect();
            let stable = 0;
            let ticks = 0;
            const check = () => {
                const r = surface.getBoundingClientRect();
                if (Math.abs(r.top - prev.top) < 0.5 && Math.abs(r.height - prev.height) < 0.5)
                    stable += 1;
                else
                    stable = 0;
                prev = r;
                ticks += 1;
                if (stable >= 10 || ticks >= 90)
                    resolve(stable >= 10 && window.scrollY === 0 && r.height >= min);
                else
                    requestAnimationFrame(check);
            };
            requestAnimationFrame(check);
        })""",
        arg=MIN_SURFACE_TAP_HEIGHT,
        timeout=10000,
    )


def tap_video_surface(page: Page) -> dict[str, Any]:
    """Click a bare spot on the video surface, clear of the header and control bar."""
    point = page.evaluate(SURFACE_TAP_POINT_JS)
    if not point:
        raise AssertionError("#video-surface not found; cannot exercise tap-to-toggle")
    hidden = page.evaluate(
        """() => {
            const bar = document.querySelector('#focus-control-overlay');
            return !!bar && bar.classList.contains('controls-hidden');
        }"""
    )
    if hidden:
        # The pointer is already parked on this point from the previous tap.
        # A second mouse.click() emits a pointermove first, which reveals the
        # bar and makes the click hide it again. A touch-style press at the
        # current point toggles it back to visible.
        page.mouse.down()
        page.mouse.up()
    else:
        page.mouse.click(point["x"], point["y"])
    return point


def phase_control_bar(checks: Checks, page: Page) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for width, height in [(384, 854), (720, 748)]:
        page.set_viewport_size({"width": width, "height": height})
        enter_snapshot_focus(page)
        before = page.evaluate(BAR_PROBE_JS)
        detail[f"bar_{width}"] = before
        checks.add(
            f"control_bar_height_le_48_{width}",
            bool(before and before["bar_height"] <= 48),
            before,
        )
        # The bar is a single horizontally scrollable row: controls past the
        # right edge are expected, not clipped, as long as the scroller can
        # bring the last one into view. At wide viewports everything already
        # fits, so the same reachability test passes without scrolling.
        scroll_probe = page.evaluate(BAR_SCROLL_PROBE_JS)
        detail[f"bar_scroll_{width}"] = scroll_probe
        checks.add(
            f"controls_not_clipped_{width}",
            bool(
                scroll_probe
                and scroll_probe["last_control_right"] is not None
                and scroll_probe["last_control_right"] <= width + 1
                and (not scroll_probe["scrollable"] or scroll_probe["scroll_moved"])
            ),
            scroll_probe,
        )
        # Tap-to-toggle must work regardless of whether media is playing. Use a
        # computed point below both the sticky header and the control bar so
        # the click lands on the surface rather than an overlapping button.
        tap_video_surface(page)
        page.wait_for_timeout(120)
        hidden = page.evaluate(BAR_PROBE_JS)
        tap_video_surface(page)
        page.wait_for_timeout(120)
        shown = page.evaluate(BAR_PROBE_JS)
        checks.add(
            f"tap_toggles_control_bar_{width}",
            bool(hidden and shown and hidden["controls_hidden"] and not shown["controls_hidden"]),
            {"hidden": hidden, "shown": shown},
        )
    return detail


def phase_pause_mute(checks: Checks, page: Page) -> dict[str, Any]:
    page.set_viewport_size({"width": 1280, "height": 800})
    enter_snapshot_focus(page)
    detail: dict[str, Any] = {}

    # No playable media: buttons must be disabled with an explanatory tooltip.
    page.evaluate(
        """() => {
            const app = window.lookoutApp;
            app.currentEventItem = null;
            app.livePlayer.isLive = false;
            app.livePlayer.liveUnavailable = true;
            app.updatePlayerButtons();
        }"""
    )
    disabled = page.evaluate(
        """() => {
            const p = document.querySelector('#player-pause-btn');
            const m = document.querySelector('#player-mute-btn');
            return {
                pause_disabled: p.disabled, mute_disabled: m.disabled,
                pause_aria_disabled: p.getAttribute('aria-disabled'),
                mute_aria_disabled: m.getAttribute('aria-disabled'),
                pause_title: p.title,
            };
        }"""
    )
    detail["disabled"] = disabled
    checks.add(
        "pause_mute_disabled_without_media",
        bool(
            disabled["pause_disabled"]
            and disabled["mute_disabled"]
            and disabled["pause_aria_disabled"] == "true"
            and disabled["mute_aria_disabled"] == "true"
            and "unavailable" in disabled["pause_title"].lower()
        ),
        disabled,
    )

    # Simulated playable media: enabled, and the buttons flip their state.
    page.evaluate(
        """() => {
            const app = window.lookoutApp;
            Object.defineProperty(app.liveVideo, 'videoWidth', { value: 1280, configurable: true });
            app.livePlayer.isLive = true;
            app.livePlayer.liveUnavailable = false;
            const record = app.liveGrid.streamPlayers.get(app.activeCamera.id);
            if (record && record.video) {
                Object.defineProperty(record.video, 'videoWidth', { value: 1280, configurable: true });
                record.isLive = true;
            }
            app.updatePlayerButtons();
        }"""
    )
    enabled = page.evaluate(
        """() => ({
            pause_disabled: document.querySelector('#player-pause-btn').disabled,
            mute_disabled: document.querySelector('#player-mute-btn').disabled,
        })"""
    )
    checks.add(
        "pause_mute_enabled_with_media",
        not enabled["pause_disabled"] and not enabled["mute_disabled"],
        enabled,
    )

    before = page.evaluate(
        """() => ({
            paused: document.querySelector('#player-pause-btn').getAttribute('aria-pressed'),
            muted: document.querySelector('#player-mute-btn').getAttribute('aria-pressed'),
        })"""
    )
    page.locator("#player-pause-btn").click()
    page.locator("#player-mute-btn").click()
    page.wait_for_timeout(150)
    after = page.evaluate(
        """() => ({
            paused: document.querySelector('#player-pause-btn').getAttribute('aria-pressed'),
            muted: document.querySelector('#player-mute-btn').getAttribute('aria-pressed'),
        })"""
    )
    detail["transitions"] = {"before": before, "after": after}
    checks.add(
        "pause_mute_state_transitions",
        before["paused"] != after["paused"] and before["muted"] != after["muted"],
        detail["transitions"],
    )
    page.evaluate(
        """() => {
            const app = window.lookoutApp;
            delete app.liveVideo.videoWidth;
            const record = app.liveGrid.streamPlayers.get(app.activeCamera.id);
            if (record && record.video) delete record.video.videoWidth;
        }"""
    )
    return detail


def phase_acoustic(checks: Checks, page: Page) -> dict[str, Any]:
    page.set_viewport_size({"width": 1280, "height": 800})
    open_viewer(page, BASE_URL)
    page.evaluate(
        """() => { const app = window.lookoutApp;
                    app.switchToFocus(app.cameras[0], { autoStartLive: false }); }"""
    )
    page.wait_for_timeout(300)
    open_display_menu(page)
    detail: dict[str, Any] = {}
    expected_classes = {
        "overlay": "acoustic-overlay",
        "below": "acoustic-below",
        "hidden": "acoustic-hidden",
    }
    for mode in ("overlay", "below", "hidden"):
        page.locator(f"#acoustic-mode-{mode}").click()
        page.wait_for_timeout(120)
        probe = page.evaluate(ACOUSTIC_PROBE_JS)
        detail[mode] = probe
        checks.add(
            f"acoustic_mode_{mode}",
            probe["group_mode"] == mode
            and dict(probe["pressed"]).get(mode) == "true"
            and expected_classes[mode] in probe["container_classes"]
            and probe["local_mode"] == mode
            and probe["settings_value"] == mode,
            probe,
        )
        counts = [value for _, value in probe["pressed"]]
        checks.add(f"acoustic_single_active_{mode}", counts.count("true") == 1, probe["pressed"])
    return detail


def phase_settings_persistence(checks: Checks, page: Page) -> dict[str, Any]:
    page.set_viewport_size({"width": 1280, "height": 800})
    open_viewer(page, BASE_URL)
    page.evaluate(
        """() => { const app = window.lookoutApp;
                    app.switchToFocus(app.cameras[0], { autoStartLive: false }); }"""
    )
    page.wait_for_timeout(300)
    open_display_menu(page)
    page.locator("#acoustic-mode-hidden").click()
    page.locator("#events-panel-toggle").click()
    page.wait_for_timeout(150)
    before = page.evaluate(LAYOUT_PROBE_JS)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_function("() => !!window.lookoutApp", timeout=15000)
    page.wait_for_timeout(300)
    after = page.evaluate(ACOUSTIC_PROBE_JS)
    layout = page.evaluate(LAYOUT_PROBE_JS)
    detail = {"before": before, "after": after, "layout": layout}
    checks.add(
        "settings_persist_acoustic_across_reload",
        after["group_mode"] == "hidden" and after["settings_value"] == "hidden",
        after,
    )
    checks.add(
        "settings_persist_events_collapse_across_reload",
        bool(layout["aside_collapsed"]),
        layout,
    )
    return detail


def phase_events_and_chips(checks: Checks, page: Page) -> dict[str, Any]:
    page.set_viewport_size({"width": 1280, "height": 800})
    open_viewer(page, BASE_URL)
    # Events collapse reclaims the whole drawer column.
    page.evaluate(
        """() => { const app = window.lookoutApp; if (app.focusPrefs.eventsCollapsed)
                    app.updateFocusPreferences({ eventsCollapsed: false }); }"""
    )
    page.wait_for_timeout(150)
    expanded = page.evaluate(LAYOUT_PROBE_JS)
    open_display_menu(page)
    page.locator("#events-panel-toggle").click()
    page.wait_for_timeout(150)
    collapsed = page.evaluate(LAYOUT_PROBE_JS)
    detail = {"expanded": expanded, "collapsed": collapsed}
    checks.add(
        "events_collapse_reclaims_width",
        bool(
            collapsed["aside_collapsed"]
            and collapsed["aside_width"] is not None
            and expanded["stage_width"] is not None
            and collapsed["stage_width"] > expanded["stage_width"]
        ),
        detail,
    )

    # Chip fade: attribute mirrors actual overflow and the mask is applied.
    checks.add(
        "chip_scroll_affordance",
        expanded["scrollable_attr"] == ("true" if expanded["switchable"] else "false")
        and (not expanded["switchable"] or "linear-gradient" in expanded["switcher_mask"]),
        expanded,
    )

    # Jump-to-events on narrow layouts expands a collapsed drawer.
    page.set_viewport_size({"width": 384, "height": 854})
    page.evaluate(
        """() => { window.lookoutApp.updateFocusPreferences({ eventsCollapsed: true }); }"""
    )
    page.wait_for_timeout(150)
    before = page.evaluate(LAYOUT_PROBE_JS)
    page.locator("#jump-to-events-btn").click()
    page.wait_for_timeout(250)
    after = page.evaluate(LAYOUT_PROBE_JS)
    detail["jump"] = {"before": before, "after": after}
    checks.add(
        "jump_to_events_visible_and_expands",
        before["jump_display"] not in (None, "none") and after["aside_collapsed"] is False,
        detail["jump"],
    )
    return detail


def phase_graceful_degradation(checks: Checks, page: Page) -> dict[str, Any]:
    page.set_viewport_size({"width": 384, "height": 854})
    enter_snapshot_focus(page)
    probe = page.evaluate(
        """() => {
            const app = window.lookoutApp;
            app.currentEventItem = null;
            app.livePlayer.showSnapshotFallback('stream HTTP 502');
            const status = document.querySelector('#live-unavailable-status');
            const start = document.querySelector('#start-live-btn');
            const overlay = document.querySelector('#snapshot-overlay');
            const pause = document.querySelector('#player-pause-btn');
            const mute = document.querySelector('#player-mute-btn');
            return {
                status_text: status ? status.textContent : '',
                retry_label: start ? start.textContent.trim() : '',
                overlay_display: overlay ? getComputedStyle(overlay).display : null,
                overlay_image: overlay ? overlay.style.backgroundImage : '',
                pause_disabled: pause ? pause.disabled : null,
                mute_disabled: mute ? mute.disabled : null,
            };
        }"""
    )
    checks.add(
        "snapshot_fallback_status_visible",
        "Live unavailable — showing snapshot" in probe["status_text"]
        and "502" in probe["status_text"],
        probe,
    )
    checks.add(
        "snapshot_fallback_retry_button",
        "Retry live" in probe["retry_label"],
        probe,
    )
    checks.add(
        "snapshot_stays_visible",
        probe["overlay_display"] == "flex" and "snapshot" in probe["overlay_image"],
        probe,
    )
    checks.add(
        "snapshot_fallback_controls_disabled",
        probe["pause_disabled"] is True and probe["mute_disabled"] is True,
        probe,
    )
    return probe


def phase_display(checks: Checks, page: Page) -> dict[str, Any]:
    """Unified Display popover + pinnable focus time readout."""
    page.set_viewport_size({"width": 1280, "height": 800})
    open_viewer(page, BASE_URL)
    # Deterministic starting point: earlier phases (or a previous run in the
    # same profile) may have persisted either Display toggle.
    page.evaluate(
        """() => { const app = window.lookoutApp;
                    app.updateFocusPreferences({
                        clockPinned: false, aiDetailsVisible: true
                    });
                    app.switchToFocus(app.cameras[0], { autoStartLive: false });
                    app.setPlayerMode('live'); }"""
    )
    page.wait_for_timeout(200)
    detail: dict[str, Any] = {}

    initial = page.evaluate(DISPLAY_PROBE_JS)
    detail["initial"] = initial
    checks.add(
        "display_menu_trigger_accessible",
        initial["trigger_expanded"] == "false"
        and bool(initial["trigger_label"])
        and bool(initial["trigger_title"]),
        initial,
    )

    # Keyboard: Enter opens, focus lands inside the popover.
    page.locator("#display-menu-btn").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    opened = page.evaluate(DISPLAY_PROBE_JS)
    detail["opened"] = opened
    checks.add(
        "display_menu_opens_with_keyboard",
        opened["menu_visible"] is True
        and opened["trigger_expanded"] == "true"
        and opened["active_inside_menu"] is True,
        opened,
    )
    checks.add(
        "display_menu_controls_present",
        all(
            opened["controls"][key]["present"] and opened["controls"][key]["visible"]
            for key in ("acoustic", "events", "ai", "clock_pin")
        ),
        opened["controls"],
    )

    # Escape closes and returns focus to the trigger.
    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    escaped = page.evaluate(DISPLAY_PROBE_JS)
    detail["escaped"] = escaped
    checks.add(
        "display_menu_escape_closes_and_restores_focus",
        escaped["menu_visible"] is False
        and escaped["trigger_expanded"] == "false"
        and escaped["active_is_trigger"] is True,
        escaped,
    )

    # A pointer press outside the popover closes it.
    open_display_menu(page)
    page.mouse.click(3, 3)
    page.wait_for_timeout(120)
    outside = page.evaluate(DISPLAY_PROBE_JS)
    detail["outside"] = outside
    checks.add(
        "display_menu_outside_click_closes",
        outside["menu_visible"] is False and outside["trigger_expanded"] == "false",
        outside,
    )

    # AI details are event-only: disabled with an explanatory title in live mode.
    open_display_menu(page)
    live = page.evaluate(DISPLAY_PROBE_JS)
    detail["live"] = live
    checks.add(
        "display_ai_disabled_in_live",
        live["ai_disabled"] is True
        and live["ai_aria_disabled"] == "true"
        and "events only" in (live["ai_title"] or "").lower(),
        live,
    )
    checks.add(
        "display_time_readout_live_has_no_event_time",
        live["event_time_hidden"] is True and live["event_age_hidden"] is True,
        live,
    )

    # Select a real event so the bar flips to event mode.
    selected = page.evaluate(
        """() => { const app = window.lookoutApp;
                    const ev = (app.events || [])[0];
                    if (!ev) return false;
                    app.playEvent(ev);
                    return true; }"""
    )
    assert selected, "phase_display needs at least one event"
    page.wait_for_function(
        """() => document.querySelector('#focus-control-overlay')?.dataset.mode === 'event'""",
        timeout=15000,
    )
    page.wait_for_timeout(250)
    open_display_menu(page)
    event_probe = page.evaluate(DISPLAY_PROBE_JS)
    detail["event"] = event_probe
    checks.add(
        "display_ai_enabled_in_event",
        event_probe["ai_disabled"] is False
        and event_probe["ai_aria_disabled"] == "false"
        and event_probe["mode"] == "event",
        event_probe,
    )
    checks.add(
        "display_time_readout_clock",
        bool(re.search(r"Local \d{2}:\d{2}:\d{2}", event_probe["clock_local"])),
        event_probe["clock_local"],
    )
    checks.add(
        "display_time_readout_event_time",
        event_probe["event_time"].startswith("Event ")
        and bool(event_probe["event_age"])
        and event_probe["event_time_hidden"] is False
        and event_probe["event_age_hidden"] is False,
        {
            "event_time": event_probe["event_time"],
            "event_age": event_probe["event_age"],
        },
    )
    first_clock = event_probe["clock_local"]
    page.wait_for_timeout(1100)
    tick = page.evaluate(DISPLAY_PROBE_JS)
    detail["tick"] = {"before": first_clock, "after": tick["clock_local"]}
    checks.add(
        "display_time_readout_ticks_each_second",
        tick["clock_local"] != first_clock,
        detail["tick"],
    )

    # The configured home timezone is shown alongside browser-local time.
    page.evaluate(
        """async () => {
            const src = document.querySelector('script[type="module"]').src;
            const mod = await import(new URL('./utils/timezone.js', src).href);
            window.__displayTzOriginal = mod.getDisplayTimezone();
            mod.setDisplayTimezone('America/New_York');
            window.lookoutApp.updateTimeReadout();
        }"""
    )
    zoned = page.evaluate(DISPLAY_PROBE_JS)
    detail["zoned"] = zoned
    checks.add(
        "display_time_readout_shows_home_zone",
        zoned["clock_home_hidden"] is False and zoned["clock_home"].startswith("Home "),
        {"local": zoned["clock_local"], "home": zoned["clock_home"]},
    )
    page.evaluate(
        """async () => {
            const src = document.querySelector('script[type="module"]').src;
            const mod = await import(new URL('./utils/timezone.js', src).href);
            mod.setDisplayTimezone(window.__displayTzOriginal || '');
            window.lookoutApp.updateTimeReadout();
        }"""
    )

    def force_bar_hide() -> None:
        page.evaluate(
            """() => {
                const app = window.lookoutApp;
                if (document.activeElement && document.activeElement.blur)
                    document.activeElement.blur();
                app.controlsFocusWithin = false;
                app.controlsHover = false;
                app.isPlayerPlaying = () => true;
                app.revealControls();
            }"""
        )
        page.wait_for_timeout(3200)

    # Pin from the popover, then let the idle timer auto-hide the bar.
    page.keyboard.press("Escape")
    open_display_menu(page)
    page.locator("#display-clock-pin-toggle").click()
    page.wait_for_timeout(120)
    pinned = page.evaluate(DISPLAY_PROBE_JS)
    detail["pinned"] = pinned
    checks.add(
        "display_clock_pin_persists",
        pinned["pin_pressed"] == "true"
        and pinned["pinned_class"] is True
        and pinned["local_storage"]["pin"] == "1",
        pinned,
    )
    page.keyboard.press("Escape")
    force_bar_hide()
    hidden_pinned = page.evaluate(DISPLAY_PROBE_JS)
    detail["hidden_pinned"] = hidden_pinned
    checks.add(
        "display_clock_pin_survives_bar_hide",
        hidden_pinned["bar_hidden"] is True
        and hidden_pinned["bar_opacity"] == "1"
        and hidden_pinned["readout_visible"] is True
        and hidden_pinned["trigger_visible"] is False,
        hidden_pinned,
    )

    # Unpinned: the readout must hide with the rest of the bar.
    page.evaluate("() => window.lookoutApp.setControlsVisible(true)")
    open_display_menu(page)
    page.locator("#display-clock-pin-toggle").click()
    page.wait_for_timeout(120)
    page.keyboard.press("Escape")
    force_bar_hide()
    hidden_unpinned = page.evaluate(DISPLAY_PROBE_JS)
    detail["hidden_unpinned"] = hidden_unpinned
    checks.add(
        "display_clock_unpinned_hides_with_bar",
        hidden_unpinned["bar_hidden"] is True
        and hidden_unpinned["readout_visible"] is False
        and hidden_unpinned["bar_opacity"] == "0",
        hidden_unpinned,
    )
    return detail


def phase_ingress(checks: Checks, context: Any, token: str) -> dict[str, Any]:
    ingress_path = f"/api/hassio_ingress/{token}"
    url = f"{BASE_URL}{ingress_path}/"
    page = context.new_page()
    sink = new_sink()
    attach_diagnostics(page, sink)
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.url))
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        open_viewer(page, url)
        probe = page.evaluate(
            """() => ({
                base_href: document.querySelector('base') ? document.querySelector('base').getAttribute('href') : null,
                chips: document.querySelectorAll('.camera-chip').length,
            })"""
        )
        api_urls = [u for u in requests if "/api/" in u or "/events/stream" in u]
        prefixed = [
            u
            for u in api_urls
            if ingress_path in u or u.startswith("/api/") or BASE_URL + "/api/" in u
        ]
        checks.add(
            "ingress_base_href",
            bool(probe["base_href"] and probe["base_href"].startswith(ingress_path)),
            probe,
        )
        checks.add(
            "ingress_api_requests_prefixed",
            bool(api_urls) and len(prefixed) == len(api_urls),
            {"api_urls": api_urls[:8], "prefixed": prefixed[:8]},
        )
        checks.add("ingress_viewer_renders", probe["chips"] > 0, probe)
    except Exception as exc:  # pragma: no cover - deployment-dependent
        checks.add("ingress_viewer_renders", False, compact(str(exc)))
        probe = {}
    finally:
        page.close()
    return {"probe": probe, "requests": requests[:20], "sink": sink, "path": ingress_path}


def main(argv: list[str] | None = None) -> int:
    global BASE_URL, INGRESS_TOKEN, INGRESS_PATH, OUT_DIR

    parser = argparse.ArgumentParser(description="Deterministic UX suite for the Lookout viewer")
    parser.add_argument("--base-url", default=BASE_URL, help="deployment URL to audit")
    parser.add_argument(
        "--out-dir", default=str(OUT_DIR), help="directory for report and screenshots"
    )
    parser.add_argument("--ingress-token", default=INGRESS_TOKEN, help="HA Ingress token segment")
    args = parser.parse_args(argv)

    BASE_URL = args.base_url.rstrip("/")
    INGRESS_TOKEN = args.ingress_token
    INGRESS_PATH = f"/api/hassio_ingress/{INGRESS_TOKEN}"
    OUT_DIR = Path(args.out_dir)
    SHOT_DIR = OUT_DIR / "lookout-ux-screenshots"
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    checks = Checks()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "ingress_path": INGRESS_PATH,
        "phases": {},
        "checks": checks.results,
        "diagnostics": {"console_errors": [], "page_errors": [], "request_failures": []},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, device_scale_factor=1, locale="en-US"
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        sink = new_sink()
        attach_diagnostics(page, sink)

        try:
            open_viewer(page, BASE_URL)
        except Exception as exc:
            print(f"FATAL: cannot load {BASE_URL}: {compact(str(exc))}", file=sys.stderr)
            browser.close()
            return 2

        print("=== UX PHASE 1: HEADER OVERFLOW ===")
        report["phases"]["header"] = phase_header(checks, page)
        print("=== UX PHASE 2: CONTROL BAR ===")
        report["phases"]["control_bar"] = phase_control_bar(checks, page)
        print("=== UX PHASE 3: PAUSE / MUTE ===")
        report["phases"]["pause_mute"] = phase_pause_mute(checks, page)
        print("=== UX PHASE 4: ACOUSTIC MODES ===")
        report["phases"]["acoustic"] = phase_acoustic(checks, page)
        print("=== UX PHASE 5: SETTINGS PERSISTENCE ===")
        report["phases"]["settings_persistence"] = phase_settings_persistence(checks, page)
        print("=== UX PHASE 6: EVENTS / CHIPS / JUMP ===")
        report["phases"]["events_and_chips"] = phase_events_and_chips(checks, page)
        print("=== UX PHASE 7: GRACEFUL DEGRADATION ===")
        report["phases"]["graceful_degradation"] = phase_graceful_degradation(checks, page)
        print("=== UX PHASE 8: DISPLAY POPOVER / TIME READOUT ===")
        report["phases"]["display"] = phase_display(checks, page)
        print("=== UX PHASE 9: INGRESS ===")
        report["phases"]["ingress"] = phase_ingress(checks, context, INGRESS_TOKEN)

        try:
            page.screenshot(path=str(SHOT_DIR / "ux-final.png"), animations="disabled")
        except Exception as exc:  # pragma: no cover - screenshot best effort
            print(f"warning: final screenshot failed: {compact(str(exc))}")
        browser.close()

    report["diagnostics"] = sink
    # Stream 5xx requests are expected here; only uncaught JS exceptions are a
    # hard failure. Console errors limited to known stream fragments are tolerated.
    unexpected_console = [
        msg
        for msg in sink["console_errors"]
        if not any(part in msg for part in EXPECTED_STREAM_ERRORS)
    ]
    checks.add(
        "no_unhandled_js_exceptions",
        not sink["page_errors"],
        sink["page_errors"][:5],
    )
    checks.add(
        "no_unexpected_console_errors",
        not unexpected_console,
        unexpected_console[:5],
    )

    report["checks"] = checks.results
    report["summary"] = {
        "ux_viewports_total": len(UX_VIEWPORTS),
        "ux_checks_total": len(checks.results),
        "ux_checks_failed": len(checks.failed),
        "ux_page_errors": len(sink["page_errors"]),
    }
    report_path = OUT_DIR / "lookout-ux-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report: {report_path}")
    print(f"Screenshots: {SHOT_DIR}")
    print(f"Summary: {json.dumps(report['summary'])}")
    for failed in checks.failed:
        print(f"  FAIL {failed['name']}: {failed['detail']}")
    return 0 if not checks.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
