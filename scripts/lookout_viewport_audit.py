#!/usr/bin/env python3
# ruff: noqa: E501 -- embedded JS probe strings are kept verbatim and readable.
"""Extended responsive / UI / interaction / Ingress audit of the live Lookout viewer.

Run with the Playwright-enabled dev extra:

    uv run python scripts/lookout_viewport_audit.py \
        --base-url https://lookout.home.ajx.codes \
        --out-dir .cache/viewport-audit \
        --ingress-token test-token

Phases
------
1. Standalone ground truth from /api/cameras and /api/events.
2. 15 viewports: camera/event presence, infinite scroll, overflow, overlaps,
   clipped text, console/network errors, plus UX observations (event label,
   stage whitespace, events fold position, chip-scroll affordance, summary
   truncation, tag wrapping, tile preview brightness, font sizes, WCAG
   contrast, unnamed controls).
3. Interaction flows at desktop + phone (view modes, chip selection, event
   selection, settings/add-camera/events-panel/camera-settings, keyboard focus,
   modal focus trap).
4. Focus-view checks on the two Pixel 9 Pro Fold viewports: enter focus, open a
   snapshot-only event, assert a <=48px unclipped control bar and a >=250px
   video surface, and verify the global pause button toggles aria-pressed.
   These are separate checks and do not change the 15-viewport or 18-step
   denominators above.
5. Home Assistant Ingress: base href, prefixed static/API/SSE requests,
   camera/event parity, console health, and iframe embedding.

Writes <out-dir>/lookout-viewport-report.json and screenshots.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

BASE_URL = "https://lookout.home.ajx.codes"
RESOLVE_IP = "192.168.1.1"  # local DNS maps the hostname here; system resolver prefers 1.1.1.1
INGRESS_TOKEN = "test-token"
INGRESS_PATH = f"/api/hassio_ingress/{INGRESS_TOKEN}"
OUT_DIR = Path("/tmp/opencode")
SHOT_DIR = OUT_DIR / "lookout-viewport-screenshots"
REPORT_PATH = OUT_DIR / "lookout-viewport-report.json"

# name, css width, css height, note
VIEWPORTS: list[tuple[str, int, int, str]] = [
    ("standard_360x640_mobile-small", 360, 640, "Small Android phone"),
    ("standard_375x667_mobile", 375, 667, "iPhone SE / classic phone"),
    ("standard_414x896_mobile-large", 414, 896, "Large phone"),
    ("standard_768x1024_tablet", 768, 1024, "Tablet portrait"),
    ("standard_1280x800_laptop", 1280, 800, "Laptop"),
    ("standard_1920x1080_desktop", 1920, 1080, "1080p desktop"),
    ("standard_2560x1080_ultrawide", 2560, 1080, "Ultrawide"),
    ("qhd_2560x1440", 2560, 1440, "1440p / QHD"),
    ("uhd_3840x2160_4k", 3840, 2160, "4K UHD"),
    ("iphone_duo_393x852_folded", 393, 852, "Rumoured foldable iPhone - cover screen (approx)"),
    ("iphone_duo_852x1112_unfolded", 852, 1112, "Rumoured foldable iPhone - inner screen (approx)"),
    ("pixel_pro_fold_384x854_cover", 384, 854, "Pixel Pro Fold - cover screen (approx)"),
    ("pixel_pro_fold_720x748_inner", 720, 748, "Pixel Pro Fold - inner screen (approx)"),
    ("z_fold_8_360x840_cover", 360, 840, "Galaxy Z Fold 8 - cover screen (approx)"),
    ("z_fold_8_664x728_inner", 664, 728, "Galaxy Z Fold 8 - inner screen (approx)"),
]

INTERACTION_VIEWPORTS = [
    ("desktop_1280x800", 1280, 800),
    ("phone_375x667", 375, 667),
]

# Derived from VIEWPORTS so the two existing Pixel 9 Pro Fold entries stay the
# single source of truth (the 15-viewport denominator is untouched).
FOCUS_VIEW_NAMES = (
    "pixel_pro_fold_384x854_cover",
    "pixel_pro_fold_720x748_inner",
)
FOCUS_VIEW_VIEWPORTS = [v for v in VIEWPORTS if v[0] in FOCUS_VIEW_NAMES]

IGNORED_CONSOLE_FRAGMENTS = ("favicon.ico", "Download the React DevTools", "[vite]")


# ---------------------------------------------------------------------------
# JS probes
# ---------------------------------------------------------------------------

LAYOUT_PROBE_JS = r"""async () => {
    const vw = window.innerWidth, vh = window.innerHeight, de = document.documentElement;
    const rect = el => {
        const r = el.getBoundingClientRect();
        return {x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1),
                h: +r.height.toFixed(1), right: +r.right.toFixed(1), bottom: +r.bottom.toFixed(1)};
    };
    const rects = sel => [...document.querySelectorAll(sel)].map(rect);
    const overlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.x, b.x)) *
                              Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y));

    const overlaps = [];
    const structural = [...rects('.app-header'), ...rects('.switcher-bar'), ...rects('.viewer-main')];
    for (let i = 0; i < structural.length; i++)
        for (let j = i + 1; j < structural.length; j++) {
            const area = overlap(structural[i], structural[j]);
            if (area > 16) overlaps.push({a: i, b: j, area_px2: +area.toFixed(1)});
        }
    for (const sel of ['.camera-grid-tile', '.event-card', '.camera-chip']) {
        const items = rects(sel);
        for (let i = 0; i < items.length; i++)
            for (let j = i + 1; j < items.length; j++) {
                const area = overlap(items[i], items[j]);
                if (area > 40) overlaps.push({selector: sel, a: i, b: j, area_px2: +area.toFixed(1)});
            }
    }

    const clipped = [];
    for (const sel of ['.cam-name', '.event-time', '.tile-title', '.badge-provider',
                       '.badge-stream', '.event-camera-tag', '.event-summary']) {
        for (const el of document.querySelectorAll(sel)) {
            const s = getComputedStyle(el);
            if (['hidden', 'clip', 'ellipsis'].includes(s.overflowX) && el.scrollWidth > el.clientWidth + 2)
                clipped.push({selector: sel, text: (el.textContent || '').trim().slice(0, 80),
                              scroll_width: el.scrollWidth, client_width: el.clientWidth});
        }
    }

    const overflow_culprits = [];
    for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.right <= vw + 2 || r.width === 0) continue;
        let p = el.parentElement, inScroller = false;
        while (p && p !== document.body) {
            const ox = getComputedStyle(p).overflowX;
            if (['auto', 'scroll', 'hidden', 'clip'].includes(ox)) { inScroller = true; break; }
            p = p.parentElement;
        }
        if (inScroller) continue;
        const cls = (typeof el.className === 'string' ? el.className : '').split(/\s+/).filter(Boolean).slice(0, 3).join('.');
        overflow_culprits.push({tag: el.tagName.toLowerCase(), cls, id: el.id || '',
                                right: +r.right.toFixed(1), width: +r.width.toFixed(1),
                                text: (el.textContent || '').trim().slice(0, 40)});
    }

    const key_elements = [];
    for (const sel of ['.app-header', '.switcher-bar', '.live-grid-container', '.timeline-section', '.filter-bar'])
        for (const el of document.querySelectorAll(sel)) key_elements.push({selector: sel, ...rect(el)});

    // --- UX observations -------------------------------------------------
    const scrubber = document.querySelector('#scrubber-container');
    let eventsLabel = null;
    if (scrubber) {
        const spans = [...scrubber.querySelectorAll('span')];
        const hit = spans.find(e => e.children.length === 0 && /\bEvents\b/.test(e.textContent || ''));
        if (hit) eventsLabel = hit.textContent.trim();
    }

    const stageEl = document.querySelector('.stage-section');
    const gridEl = document.querySelector('.live-grid-container');
    const stage = stageEl ? rect(stageEl) : null;
    const grid = gridEl ? rect(gridEl) : null;
    const stageWhitespacePx = (stage && grid) ? +Math.max(0, stage.bottom - grid.bottom).toFixed(1) : null;
    const stageWhitespaceRatio = (stage && stage.h > 0) ? +(stageWhitespacePx / stage.h).toFixed(3) : null;

    const timelineEl = document.querySelector('.timeline-section');
    const timelineTop = timelineEl ? +timelineEl.getBoundingClientRect().top.toFixed(1) : null;
    const eventsBelowFold = timelineTop !== null && timelineTop > vh + 4;

    const sw = document.querySelector('.switcher-bar');
    let chipScroll = null;
    if (sw) {
        const s = getComputedStyle(sw);
        chipScroll = {
            scrollWidth: sw.scrollWidth, clientWidth: sw.clientWidth,
            scrollable: sw.scrollWidth > sw.clientWidth + 2, overflowX: s.overflowX,
            maskImage: s.maskImage || 'none', gradientBackground: /gradient/.test(s.backgroundImage),
            hintElements: document.querySelectorAll('[class*=hint],[class*=fade],[class*=arrow],[class*=scroll-indicator]').length
        };
    }

    const summaries = [...document.querySelectorAll('.event-summary')];
    const truncatedSummaries = summaries.filter(e => e.scrollWidth > e.clientWidth + 2);
    const summaryTruncation = {
        total: summaries.length, truncated: truncatedSummaries.length,
        withTitle: truncatedSummaries.filter(e => e.hasAttribute('title')).length
    };
    const tagLines = [...document.querySelectorAll('.event-camera-tag')].map(e => {
        const lh = parseFloat(getComputedStyle(e).lineHeight) || 14;
        return Math.max(1, Math.round(e.getBoundingClientRect().height / lh));
    });

    const fontSizes = {};
    for (const sel of ['body', '.event-summary', '.event-camera-tag', '.cam-name', '.brand-logo']) {
        const el = document.querySelector(sel);
        fontSizes[sel] = el ? +parseFloat(getComputedStyle(el).fontSize).toFixed(1) : null;
    }

    // --- WCAG contrast ---------------------------------------------------
    const parseColor = c => {
        const m = String(c || '').match(/[\d.]+/g);
        if (!m || m.length < 3) return null;
        return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1];
    };
    const lum = c => {
        const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
    };
    const cr = (a, b) => {
        const l1 = lum(a), l2 = lum(b);
        return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    };
    const effectiveBg = el => {
        let cur = el;
        while (cur && cur !== document.documentElement) {
            const c = parseColor(getComputedStyle(cur).backgroundColor);
            if (c && c[3] > 0.9) return c;
            cur = cur.parentElement;
        }
        return [255, 255, 255, 1];
    };
    const lowContrast = [];
    for (const sel of ['.brand-logo', '.cam-name', '.btn', '.event-summary', '.event-time',
                       '.badge-provider', '.badge-stream', '.event-camera-tag']) {
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            const s = getComputedStyle(el);
            if (s.visibility === 'hidden' || s.display === 'none') continue;
            const fg = parseColor(s.color), bg = effectiveBg(el);
            if (!fg || !bg) continue;
            const ratio = cr(fg, bg);
            const fs = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700;
            const threshold = (fs >= 24 || (fs >= 18.66 && bold)) ? 3 : 4.5;
            if (ratio < threshold)
                lowContrast.push({selector: sel, text: (el.textContent || '').trim().slice(0, 40),
                                  ratio: +ratio.toFixed(2), threshold, color: s.color,
                                  background: `rgb(${bg.slice(0, 3).join(',')})`});
        }
    }
    lowContrast.sort((a, b) => a.ratio - b.ratio);

    // --- accessible names ------------------------------------------------
    const unnamed = [];
    for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex="-1"])')) {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;
        let name = el.getAttribute('aria-label') || el.getAttribute('title') || '';
        if (!name && el.tagName === 'INPUT' && el.labels && el.labels.length) name = el.labels[0].textContent.trim();
        if (!name) name = el.getAttribute('alt') || '';
        if (!name) name = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!name) unnamed.push({tag: el.tagName.toLowerCase(), id: el.id || '',
                                 cls: (typeof el.className === 'string' ? el.className : '').slice(0, 60)});
    }

    // --- tile preview brightness -----------------------------------------
    const previews = await Promise.all([...document.querySelectorAll('.camera-grid-tile')].map(async tile => {
        const ov = tile.querySelector('.tile-overlay');
        const vid = tile.querySelector('video');
        let url = '';
        const m = ov ? getComputedStyle(ov).backgroundImage.match(/url\(["']?([^"')]+)["']?\)/) : null;
        if (m) url = m[1];
        if (!url && vid && vid.poster) url = vid.poster;
        if (!url) return {id: tile.dataset.cameraId || '', url: null, meanLuma: null};
        try {
            const img = new Image();
            const load = new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
            await Promise.race([load, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 3000))]);
            const c = document.createElement('canvas'); c.width = 32; c.height = 32;
            const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0, 32, 32);
            const d = ctx.getImageData(0, 0, 32, 32).data;
            let sum = 0;
            for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
            return {id: tile.dataset.cameraId || '', url, meanLuma: +(sum / (d.length / 4)).toFixed(1)};
        } catch (e) {
            return {id: tile.dataset.cameraId || '', url, meanLuma: null, error: String(e).slice(0, 60)};
        }
    }));

    return {
        inner_width: vw, inner_height: vh, device_pixel_ratio: window.devicePixelRatio,
        doc_scroll_width: de.scrollWidth, body_scroll_width: document.body.scrollWidth,
        horizontal_overflow_px: Math.max(0, de.scrollWidth - vw),
        key_elements, overlaps, clipped_text: clipped.slice(0, 20), clipped_text_total: clipped.length,
        overflow_culprits: overflow_culprits.slice(0, 10),
        events_label: eventsLabel, stage_whitespace_px: stageWhitespacePx,
        stage_whitespace_ratio: stageWhitespaceRatio, timeline_top: timelineTop,
        events_below_fold: eventsBelowFold,
        events_jump_control: !!document.querySelector('#jump-to-events-btn'),
        chip_scroll: chipScroll,
        summary_truncation: summaryTruncation,
        camera_tag_lines: {max: tagLines.length ? Math.max(...tagLines) : 0,
                           multi_line: tagLines.filter(n => n > 1).length, total: tagLines.length},
        font_sizes: fontSizes, low_contrast: lowContrast.slice(0, 12),
        low_contrast_total: lowContrast.length, unnamed_controls: unnamed.slice(0, 12),
        unnamed_controls_total: unnamed.length, tile_previews: previews
    };
}"""

# Focus-view probe: compact auto-hiding bar + snapshot-only stage geometry.
FOCUS_PROBE_JS = r"""() => {
    const vw = window.innerWidth;
    const overlay = document.querySelector('#focus-control-overlay');
    const surface = document.querySelector('.video-surface');
    const scroller = document.querySelector('#control-bar-scroll');
    const pauseBtn = document.querySelector('#global-pause-btn');
    const barRect = overlay ? overlay.getBoundingClientRect() : null;

    // Only count controls actually inside the scroll viewport as visible.
    const scrollerRect = scroller ? scroller.getBoundingClientRect() : null;
    const controls = overlay ? [...overlay.querySelectorAll('button')].filter(el => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') return false;
        if (scrollerRect && scroller.contains(el) &&
            (r.left < scrollerRect.left - 1 || r.right > scrollerRect.right + 1)) return false;
        return true;
    }).map(el => {
        const r = el.getBoundingClientRect();
        return {id: el.id || '', label: el.getAttribute('aria-label') || el.title || '',
                right: +r.right.toFixed(1)};
    }) : [];

    const snapCard = document.querySelector('#event-snapshot-card');
    return {
        inner_width: vw,
        mode: overlay ? (overlay.dataset.mode || null) : null,
        controls_hidden: !!(overlay && overlay.classList.contains('controls-hidden')),
        bar_height: barRect ? +barRect.height.toFixed(1) : null,
        controls: controls,
        controls_right_max: controls.length ? Math.max(...controls.map(c => c.right)) : null,
        surface_height: surface ? +surface.getBoundingClientRect().height.toFixed(1) : null,
        scrollable: scroller ? scroller.scrollWidth > scroller.clientWidth + 2 : false,
        global_pause_present: !!pauseBtn,
        global_pause_pressed: pauseBtn ? pauseBtn.getAttribute('aria-pressed') : null,
        snapshot_only: !!document.querySelector('.video-container.snapshot-only'),
        snapshot_visible: !!snapCard && getComputedStyle(snapCard).display !== 'none'
    };
}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compact(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


def fetch_expectations(page: Page) -> dict[str, Any]:
    cams = page.evaluate(
        """async () => {
            const r = await fetch('/api/cameras');
            if (!r.ok) throw new Error('cameras API ' + r.status);
            return (await r.json()).cameras || [];
        }"""
    )
    evs = page.evaluate(
        """async () => {
            const r = await fetch('/api/events');
            if (!r.ok) throw new Error('events API ' + r.status);
            return await r.json();
        }"""
    )
    enabled = [c for c in cams if c.get("enabled", True)]
    return {
        "camera_ids": [c["id"] for c in enabled],
        "camera_names": {c["id"]: c.get("name") or c["id"] for c in enabled},
        "event_ids": [e["id"] for e in evs.get("events", [])],
        "event_total": evs.get("total"),
        "event_page_size": evs.get("limit"),
    }


def attach_diagnostics(page: Page, sink: dict[str, list[Any]]) -> None:
    def on_console(msg: Any) -> None:
        text = compact(msg.text)
        if any(frag in text for frag in IGNORED_CONSOLE_FRAGMENTS):
            return
        if msg.type == "error":
            sink["console_errors"].append(text)
        elif msg.type == "warning":
            sink["console_warnings"].append(text)

    def on_page_error(exc: Any) -> None:
        sink["page_errors"].append(compact(str(exc)))

    def on_request_failed(req: Any) -> None:
        sink["failed_requests"].append({"url": req.url[:300], "failure": str(req.failure)})

    def on_response(resp: Any) -> None:
        if resp.status >= 400:
            sink["bad_responses"].append({"url": resp.url[:300], "status": str(resp.status)})

    def on_request(req: Any) -> None:
        if req.resource_type in ("fetch", "eventsource", "xhr", "document", "stylesheet", "script"):
            sink["requests"].append({"url": req.url[:300], "type": req.resource_type})

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)
    page.on("request", on_request)
    sink["_listeners"] = [on_console, on_page_error, on_request_failed, on_response, on_request]


def detach_diagnostics(page: Page, sink: dict[str, list[Any]]) -> None:
    events = ["console", "pageerror", "requestfailed", "response", "request"]
    for event, handler in zip(events, sink.get("_listeners", []), strict=False):
        page.remove_listener(event, handler)


def new_sink() -> dict[str, list[Any]]:
    return {
        "console_errors": [],
        "console_warnings": [],
        "page_errors": [],
        "failed_requests": [],
        "bad_responses": [],
        "requests": [],
    }


def scroll_timeline_to_load_all(page: Page, expected: int, result: dict[str, Any]) -> int:
    loaded = page.locator(".event-card").count()
    for _ in range(8):
        if loaded >= expected:
            break
        page.evaluate(
            "() => { const el = document.querySelector('#timeline-list'); "
            "if (el) el.scrollTop = el.scrollHeight; }"
        )
        page.wait_for_timeout(300)
        new_loaded = page.locator(".event-card").count()
        if new_loaded == loaded:
            break
        loaded = new_loaded
    result["events_after_scroll"] = loaded
    if not page.locator(".timeline-load-more-sentinel").count() and loaded < expected:
        result["issues"].append(
            f"Timeline stopped at {loaded}/{expected} events with no load-more sentinel"
        )
    return loaded


def dom_ids(page: Page, selector: str, attr: str, result: dict[str, Any]) -> list[str]:
    try:
        return page.eval_on_selector_all(
            selector,
            "(els, attr) => els.map(e => e.dataset[attr]).filter(Boolean)",
            attr,
        )
    except Exception as exc:
        result["issues"].append(f"Could not read {selector}: {compact(str(exc))}")
        return []


def apply_observations(
    result: dict[str, Any], probe: dict[str, Any], expectations: dict[str, Any]
) -> None:
    label = probe.get("events_label")
    total = expectations["event_total"]
    if label:
        match = re.search(r"(\d+)\s+Events", label, re.I)
        if match and total is not None and int(match.group(1)) != int(total):
            result["warnings"].append(
                f'Scrubber label "{label}" shows loaded page count, not the {total} events on the server'
            )

    ratio = probe.get("stage_whitespace_ratio")
    if ratio is not None and ratio > 0.35:
        result["warnings"].append(
            f"Stage is {probe.get('stage_whitespace_px')}px ({ratio:.0%}) empty space below the camera grid"
        )

    # AC8 accepts a jump/toggle/reorder control as the mitigation for events
    # starting below the fold, so only warn when that control is absent.
    if probe.get("events_below_fold") and not probe.get("events_jump_control"):
        result["warnings"].append(
            f"Events panel begins {probe.get('timeline_top')}px down, below the "
            f"{probe.get('inner_height')}px viewport (users must scroll past the grid)"
        )

    chip = probe.get("chip_scroll") or {}
    if chip.get("scrollable"):
        has_affordance = (
            chip.get("maskImage") not in (None, "none")
            or chip.get("gradientBackground")
            or chip.get("hintElements", 0) > 0
        )
        if not has_affordance:
            result["warnings"].append(
                f"Camera chip strip scrolls ({chip['scrollWidth']}px content / "
                f"{chip['clientWidth']}px visible) but has no visible scroll affordance"
            )

    trunc = probe.get("summary_truncation") or {}
    if trunc.get("truncated", 0):
        uncovered = trunc["truncated"] - trunc.get("withTitle", 0)
        result["warnings"].append(
            f"{trunc['truncated']}/{trunc.get('total')} event summaries are ellipsized"
            + (
                f"; {uncovered} lack a title tooltip (unreadable on touch)"
                if uncovered
                else "; all have title tooltips"
            )
        )

    tags = probe.get("camera_tag_lines") or {}
    if tags.get("multi_line", 0):
        result["warnings"].append(
            f"{tags['multi_line']}/{tags.get('total')} event camera tags wrap to "
            f"up to {tags.get('max')} lines in the fixed-width events panel"
        )

    small = {k: v for k, v in (probe.get("font_sizes") or {}).items() if v is not None and v < 11}
    if small:
        result["warnings"].append(f"Text below 11px: {small}")

    if probe.get("low_contrast_total", 0):
        worst = (probe.get("low_contrast") or [])[:3]
        result["warnings"].append(
            f"{probe['low_contrast_total']} low-contrast text element(s) vs WCAG AA: {worst}"
        )

    if probe.get("unnamed_controls_total", 0):
        result["warnings"].append(
            f"{probe['unnamed_controls_total']} interactive control(s) without an accessible name: "
            f"{probe.get('unnamed_controls', [])[:3]}"
        )

    blank = [
        p
        for p in (probe.get("tile_previews") or [])
        if p.get("meanLuma") is not None and p["meanLuma"] < 15
    ]
    if blank:
        result["warnings"].append(
            f"{len(blank)} camera tile preview(s) are black/blank: "
            f"{[(p['id'], p['meanLuma']) for p in blank]}"
        )


# ---------------------------------------------------------------------------
# Phase 2: viewport audit
# ---------------------------------------------------------------------------


def audit_viewport(
    page: Page, name: str, width: int, height: int, note: str, expectations: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "requested": {"width": width, "height": height},
        "note": note,
        "issues": [],
        "warnings": [],
    }
    sink = new_sink()
    attach_diagnostics(page, sink)

    page.set_viewport_size({"width": width, "height": height})
    started = time.time()
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".camera-chip", timeout=45000)
        page.wait_for_selector(".camera-grid-tile", timeout=45000)
        page.wait_for_selector(".event-card", timeout=45000)
        page.wait_for_timeout(1000)
    except Exception as exc:
        result["issues"].append(f"Page load/selector failure: {compact(str(exc))}")
    result["load_seconds"] = round(time.time() - started, 2)

    try:
        scroll_timeline_to_load_all(page, len(expectations["event_ids"]), result)
    except Exception as exc:
        result["warnings"].append(f"Infinite-scroll check failed: {compact(str(exc))}")

    chips = dom_ids(page, ".camera-chip", "cameraId", result)
    tiles = dom_ids(page, ".camera-grid-tile", "cameraId", result)
    cards = dom_ids(page, ".event-card", "eventId", result)

    expected_cams = set(expectations["camera_ids"])
    expected_evs = set(expectations["event_ids"])
    result["counts"] = {
        "expected_cameras": len(expected_cams),
        "camera_chips": len(chips),
        "camera_tiles": len(tiles),
        "expected_events_page": len(expected_evs),
        "event_cards": len(cards),
    }
    for label, found in (("switcher chips", chips), ("live grid tiles", tiles)):
        missing = sorted(expected_cams - set(found))
        if missing:
            result["issues"].append(f"Cameras missing from {label}: {missing}")
    missing_cards = sorted(expected_evs - set(cards))
    if missing_cards:
        result["issues"].append(
            f"Events missing from timeline ({len(missing_cards)} of {len(expected_evs)}): {missing_cards[:6]}"
        )

    # Chip visibility / horizontal placement.
    try:
        geom = page.evaluate(
            """() => [...document.querySelectorAll('.camera-chip')].map(el => {
                const r = el.getBoundingClientRect(), s = getComputedStyle(el);
                return {id: el.dataset.cameraId || '',
                        visible: !!(r.width && r.height) && s.visibility !== 'hidden' && s.display !== 'none',
                        x: Math.round(r.x), right: Math.round(r.right)};
            })"""
        )
        offscreen = [g for g in geom if g["visible"] and (g["x"] < -2 or g["right"] > width + 2)]
        hidden = [g for g in geom if not g["visible"]]
        switcher = page.evaluate(
            """() => { const el = document.querySelector('.switcher-bar'); if (!el) return null;
                return {scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
                        overflowX: getComputedStyle(el).overflowX}; }"""
        )
        if offscreen:
            scrollable = bool(
                switcher
                and switcher.get("overflowX") in ("auto", "scroll")
                and switcher["scrollWidth"] > switcher["clientWidth"]
            )
            msg = f"{len(offscreen)} camera chip(s) start outside the viewport: {[g['id'] for g in offscreen[:6]]}"
            (result["warnings"] if scrollable else result["issues"]).append(
                msg
                + (
                    "; switcher bar is horizontally scrollable, still reachable"
                    if scrollable
                    else "; switcher bar is NOT scrollable - chips unreachable"
                )
            )
        if hidden:
            result["issues"].append(
                f"{len(hidden)} camera chip(s) zero-size/hidden: {[g['id'] for g in hidden[:6]]}"
            )
    except Exception as exc:
        result["warnings"].append(f"Chip geometry check failed: {compact(str(exc))}")

    try:
        probe = page.evaluate(LAYOUT_PROBE_JS)
    except Exception as exc:
        result["warnings"].append(f"Layout/UX probe failed: {compact(str(exc))}")
        probe = {}
    result["geometry"] = probe
    apply_observations(result, probe, expectations)

    overflow = probe.get("horizontal_overflow_px", 0)
    if overflow > 2:
        culprits = probe.get("overflow_culprits", [])
        detail = json.dumps(culprits[:5])[:600] if culprits else "no culprit identified"
        result["issues"].append(f"Horizontal page overflow: {overflow}px; culprits: {detail}")

    if probe.get("overlaps"):
        result["issues"].append(
            f"{len(probe['overlaps'])} overlapping element pair(s): {json.dumps(probe['overlaps'][:4])[:400]}"
        )
    if probe.get("clipped_text_total", 0):
        result["warnings"].append(
            f"{probe['clipped_text_total']} text element(s) clipped: "
            f"{json.dumps(probe.get('clipped_text', [])[:4])[:400]}"
        )

    if sink["page_errors"]:
        result["issues"].append(
            f"{len(sink['page_errors'])} uncaught JS exception(s): {sink['page_errors'][:3]}"
        )
    if sink["console_errors"]:
        counts = Counter(sink["console_errors"])
        result["issues"].append(
            f"{len(sink['console_errors'])} console error(s): "
            f"{[f'{n}x {m}' for m, n in counts.most_common(3)]}"
        )
    if sink["console_warnings"]:
        result["warnings"].append(
            f"{len(sink['console_warnings'])} console warning(s): {sink['console_warnings'][:3]}"
        )

    api_failures = [r for r in sink["bad_responses"] if "/api/" in r["url"]]
    if api_failures:
        counts = Counter(f"{r['status']} {r['url'].split('?')[0]}" for r in api_failures)
        result["issues"].append(
            f"{len(api_failures)} failed API response(s): "
            f"{[f'{n}x {m}' for m, n in counts.most_common(3)]}"
        )
    media_failures = [
        r
        for r in sink["bad_responses"] + sink["failed_requests"]
        if not ("/api/" in r.get("url", "") and "status" in r)
    ]
    if media_failures:
        counts = Counter(
            f"{r.get('status') or r.get('failure')} {r['url'].split('?')[0]}"
            for r in media_failures
        )
        result["warnings"].append(
            f"{len(media_failures)} media/other request failure(s): "
            f"{[f'{n}x {m}' for m, n in counts.most_common(3)]}"
        )

    result["diagnostics"] = {
        k: sink[k][:20]
        for k in (
            "console_errors",
            "console_warnings",
            "page_errors",
            "bad_responses",
            "failed_requests",
        )
    }
    result["diagnostic_counts"] = {
        k: len(sink[k])
        for k in (
            "console_errors",
            "console_warnings",
            "page_errors",
            "bad_responses",
            "failed_requests",
        )
    }

    shot = SHOT_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(shot), full_page=False, animations="disabled")
        result["screenshot"] = str(shot)
    except Exception as exc:
        result["warnings"].append(f"Screenshot failed: {compact(str(exc))}")

    detach_diagnostics(page, sink)
    return result


# ---------------------------------------------------------------------------
# Phase 3: interaction flows
# ---------------------------------------------------------------------------


def ensure_grid_mode(page: Page) -> None:
    cls = page.locator("#view-grid-btn").get_attribute("class") or ""
    if "active" not in cls:
        page.locator("#view-grid-btn").click()
        page.wait_for_timeout(300)


def reveal_focus_controls(page: Page) -> None:
    """Make the auto-hiding focus control bar clickable before interacting.

    The bar hides itself after ~3s of inactivity and is inert while hidden, so
    a plain locator.click() on a control inside it can time out late in the
    interaction sweep. Move the pointer over the video surface -- the product's
    own reveal gesture -- and fall back to revealControls() when the surface
    has no laid-out box yet. The bar lives inside the focus stage, which is
    display:none in grid view, so ensure focus view first.
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


def run_interaction_suite(page: Page, label: str, expectations: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"viewport": label, "steps": [], "issues": [], "warnings": []}
    sink = new_sink()
    attach_diagnostics(page, sink)
    page.set_viewport_size(
        {"width": 1280 if "desktop" in label else 375, "height": 800 if "desktop" in label else 667}
    )
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_selector(".event-card", timeout=45000)
    page.wait_for_timeout(600)

    def step(name: str, fn: Callable[[], Any]) -> None:
        try:
            detail = fn()
            result["steps"].append({"name": name, "ok": True, "detail": detail})
        except Exception as exc:
            result["steps"].append({"name": name, "ok": False, "detail": compact(str(exc))})
            result["issues"].append(f"{name}: {compact(str(exc))}")
        with contextlib.suppress(Exception):
            ensure_grid_mode(page)

    def chip_toggle() -> str:
        before = page.locator(".camera-grid-tile").count()
        page.locator(".camera-chip").first.click()
        page.wait_for_timeout(350)
        after = page.locator(".camera-grid-tile").count()
        page.locator(".camera-chip").first.click()
        page.wait_for_timeout(350)
        restored = page.locator(".camera-grid-tile").count()
        if after != before - 1 or restored != before:
            raise AssertionError(f"tile counts before={before} after={after} restored={restored}")
        return f"{before} -> {after} -> {restored}"

    def view_modes() -> str:
        page.locator("#view-focus-btn").click()
        page.wait_for_timeout(400)
        focus_ok = page.locator("#focus-stage").is_visible()
        grid_hidden = not page.locator("#live-grid-section").is_visible()
        page.locator("#view-grid-btn").click()
        page.wait_for_timeout(400)
        grid_ok = page.locator("#live-grid-section").is_visible()
        if not (focus_ok and grid_hidden and grid_ok):
            raise AssertionError(
                f"focus={focus_ok} gridHidden={grid_hidden} gridRestored={grid_ok}"
            )
        return "focus entered and grid restored"

    def event_selection() -> str:
        page.locator(".event-card").first.click()
        page.wait_for_timeout(600)
        details = page.locator("#focus-ai-details-card").is_visible()
        video = page.locator("#event-video").is_visible()
        snapshot = page.locator("#event-snapshot-card").is_visible()
        if not (details and (video or snapshot)):
            raise AssertionError(f"details={details} video={video} snapshot={snapshot}")
        return f"details={details} video={video} snapshot={snapshot}"

    def settings_modal() -> str:
        page.locator("#open-settings-btn").click()
        page.wait_for_timeout(300)
        opened = "open" in (page.locator("#settings-modal").get_attribute("class") or "")
        nav = page.locator("#settings-modal .settings-nav a").count()
        page.locator("#modal-close-btn").click()
        page.wait_for_timeout(300)
        closed = "open" not in (page.locator("#settings-modal").get_attribute("class") or "")
        if not (opened and nav >= 5 and closed):
            raise AssertionError(f"opened={opened} nav={nav} closed={closed}")
        return f"opened={opened} navLinks={nav} closed={closed}"

    def add_camera_dropdown() -> str:
        page.locator("#add-camera-dropdown-btn").click()
        page.wait_for_timeout(200)
        opened = page.locator("#add-camera-dropdown").is_visible()
        page.locator("#add-camera-dropdown-btn").click()
        page.wait_for_timeout(200)
        closed = not page.locator("#add-camera-dropdown").is_visible()
        if not (opened and closed):
            raise AssertionError(f"opened={opened} closed={closed}")
        return f"opened={opened} closed={closed}"

    def events_panel_toggle() -> str:
        # The toggle lives in the header-anchored Display popover (same ID), so
        # its trigger stays reachable without revealing the auto-hiding bar.
        trigger = page.locator("#display-menu-btn")
        if trigger.get_attribute("aria-expanded") != "true":
            trigger.click()
            page.wait_for_selector("#display-menu:not([hidden])", state="visible")
        # Keep the bar revealed while toggling inside the popover. With the
        # popover open, displayMenuOpen already blocks the idle timer, but an
        # explicit reveal makes the click robust regardless of timing.
        page.evaluate(
            "() => { const app = window.lookoutApp; "
            "if (app && typeof app.revealControls === 'function') app.revealControls(); }"
        )
        before = "collapsed" in (page.locator(".timeline-section").get_attribute("class") or "")
        page.locator("#events-panel-toggle").click()
        page.wait_for_timeout(250)
        after = "collapsed" in (page.locator(".timeline-section").get_attribute("class") or "")
        cards_visible = page.locator(".timeline-list").is_visible()
        page.locator("#events-panel-toggle").click()
        page.wait_for_timeout(250)
        restored = "collapsed" in (page.locator(".timeline-section").get_attribute("class") or "")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        if after == before or (after and cards_visible) or restored != before:
            raise AssertionError(
                f"before={before} after={after} cardsVisible={cards_visible} restored={restored}"
            )
        return f"collapsed={after} cardsHidden={not cards_visible} restored={not restored}"

    def camera_settings_modal() -> str:
        page.locator("#view-focus-btn").click()
        page.wait_for_timeout(400)
        page.locator("#focus-settings-btn").click()
        page.wait_for_timeout(300)
        visible = page.locator("#camera-settings-modal").is_visible()
        page.locator("#camera-settings-modal-close").click()
        page.wait_for_timeout(250)
        closed = not page.locator("#camera-settings-modal").is_visible()
        if not (visible and closed):
            raise AssertionError(f"visible={visible} closed={closed}")
        return f"visible={visible} closed={closed}"

    def keyboard_focus() -> str:
        page.locator(".brand-logo").focus()
        checks = []
        for _ in range(8):
            page.keyboard.press("Tab")
            info = page.evaluate(
                """() => { const el = document.activeElement; if (!el || el === document.body) return null;
                    const s = getComputedStyle(el);
                    return {tag: el.tagName.toLowerCase(), id: el.id || '',
                            outline: s.outlineStyle, outlineWidth: s.outlineWidth,
                            shadow: s.boxShadow, visible: el.matches(':focus-visible')}; }"""
            )
            if info:
                checks.append(info)
        bad = [c for c in checks if c["outline"] == "none" and c["shadow"] == "none"]
        if bad:
            raise AssertionError(
                f"{len(bad)}/{len(checks)} focused controls show no outline: {bad[:3]}"
            )
        return f"{len(checks)} tab stops with visible focus"

    def modal_focus_trap() -> str:
        page.locator("#open-settings-btn").click()
        page.wait_for_timeout(300)
        initial_inside = page.evaluate("() => !!document.activeElement.closest('#settings-modal')")
        escaped = 0
        for _ in range(14):
            page.keyboard.press("Tab")
            inside = page.evaluate("() => !!document.activeElement.closest('#settings-modal')")
            if not inside:
                escaped += 1
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        escape_closed = "open" not in (page.locator("#settings-modal").get_attribute("class") or "")
        if not initial_inside:
            result["warnings"].append(
                "Settings modal does not move keyboard focus into itself on open"
            )
        if escaped:
            result["warnings"].append(
                f"Settings modal has no focus trap: focus was outside the dialog on "
                f"{escaped}/14 tab presses"
            )
        if not escape_closed:
            result["warnings"].append("Escape does not close the settings modal")
        if not escape_closed:
            page.locator("#modal-close-btn").click()
            page.wait_for_timeout(200)
        return (
            f"initialFocusInside={initial_inside}, escapedTabs={escaped}/14, "
            f"escapeClosed={escape_closed}"
        )

    step("camera chip multi-select toggles grid tiles", chip_toggle)
    step("grid/focus view mode toggle", view_modes)
    step("event card selection opens detail player", event_selection)
    step("settings modal opens/closes", settings_modal)
    step("add-camera dropdown opens/closes", add_camera_dropdown)
    step("events panel collapse/expand", events_panel_toggle)
    step("camera settings modal opens/closes", camera_settings_modal)
    step("keyboard tab focus is visible", keyboard_focus)
    step("settings modal traps focus", modal_focus_trap)

    result["diagnostic_counts"] = {
        k: len(sink[k])
        for k in ("console_errors", "page_errors", "bad_responses", "failed_requests")
    }
    result["console_errors"] = sink["console_errors"][:10]
    if sink["console_errors"]:
        result["warnings"].append(
            f"{len(sink['console_errors'])} console error(s) during interactions: {sink['console_errors'][:3]}"
        )
    if sink["page_errors"]:
        result["issues"].append(f"JS exception(s) during interactions: {sink['page_errors'][:3]}")

    shot = SHOT_DIR / f"interaction_{label}.png"
    try:
        ensure_grid_mode(page)
        page.screenshot(path=str(shot), animations="disabled")
        result["screenshot"] = str(shot)
    except Exception as exc:
        result["warnings"].append(f"Interaction screenshot failed: {compact(str(exc))}")

    detach_diagnostics(page, sink)
    return result


# ---------------------------------------------------------------------------
# Focus-view phase: Pixel 9 Pro Fold cover + inner screens
# ---------------------------------------------------------------------------


def audit_focus_view(
    page: Page, name: str, width: int, height: int, expectations: dict[str, Any]
) -> dict[str, Any]:
    """Enter focus, open a snapshot-only event, and check the compact bar.

    These checks are reported separately from VIEWPORTS/INTERACTION_VIEWPORTS;
    they never add to the 15-viewport or 18-step denominators.
    """
    result: dict[str, Any] = {
        "name": name,
        "requested": {"width": width, "height": height},
        "checks": {},
        "issues": [],
        "warnings": [],
    }
    page.set_viewport_size({"width": width, "height": height})
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".camera-chip", timeout=45000)
        page.wait_for_selector(".event-card", timeout=45000)
        page.locator("#view-focus-btn").click()
        page.wait_for_timeout(400)
        selected = page.evaluate(
            """() => {
                const app = window.lookoutApp;
                if (!app || !Array.isArray(app.events)) return false;
                const ev = app.events.find(e => {
                    const purged = !!(e.metadata && e.metadata.media_purged);
                    const media = e.media_uri || e.clip_url || e.video_url ||
                                  (e.media_type === 'video');
                    return purged || !media;
                });
                if (!ev) return false;
                app.playEvent(ev);
                return ev.id;
            }"""
        )
        page.wait_for_timeout(700)
        result["selected_event_id"] = selected
        if not selected:
            result["warnings"].append(
                "No snapshot-only event available; layout checks ran on the current focus media"
            )
        probe = page.evaluate(FOCUS_PROBE_JS)
        result["probe"] = probe

        checks = result["checks"]
        checks["control_bar_height_le_48"] = bool(
            probe.get("bar_height") is not None and probe["bar_height"] <= 48
        )
        checks["controls_not_clipped"] = bool(
            probe.get("controls")
            and probe.get("controls_right_max") is not None
            and probe["controls_right_max"] <= width + 1
        )
        checks["video_surface_height_ge_250"] = bool(
            probe.get("surface_height") is not None and probe["surface_height"] >= 250
        )
        checks["global_pause_present"] = bool(probe.get("global_pause_present"))
        if not probe.get("snapshot_only"):
            result["warnings"].append("Snapshot-only event could not be selected")

        before = page.locator("#global-pause-btn").get_attribute("aria-pressed")
        page.locator("#global-pause-btn").click()
        page.wait_for_timeout(200)
        after = page.locator("#global-pause-btn").get_attribute("aria-pressed")
        checks["global_pause_toggles"] = before != after
        page.locator("#global-pause-btn").click()
        page.wait_for_timeout(200)
        result["pause_aria_pressed"] = {"before": before, "after": after}
    except Exception as exc:
        result["issues"].append(f"Focus-view phase failed: {compact(str(exc))}")

    for check_name, ok in result["checks"].items():
        if not ok:
            result["issues"].append(f"focus check failed: {check_name}")

    try:
        shot = SHOT_DIR / f"focus_{name}.png"
        page.screenshot(path=str(shot), full_page=False, animations="disabled")
        result["screenshot"] = str(shot)
    except Exception as exc:
        result["warnings"].append(f"Focus screenshot failed: {compact(str(exc))}")
    return result


# ---------------------------------------------------------------------------
# Phase 4: Home Assistant Ingress
# ---------------------------------------------------------------------------


def audit_ingress(context: Any, expectations: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"issues": [], "warnings": [], "checks": {}}
    page = context.new_page()
    sink = new_sink()
    attach_diagnostics(page, sink)

    url = f"{BASE_URL}{INGRESS_PATH}/"
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_selector(".event-card", timeout=45000)
    page.wait_for_timeout(800)

    base_href = page.locator("base").get_attribute("href") if page.locator("base").count() else None
    result["checks"]["base_href"] = base_href
    if base_href != f"{INGRESS_PATH}/":
        result["issues"].append(f"Unexpected base href under Ingress: {base_href}")

    chips = dom_ids(page, ".camera-chip", "cameraId", result)
    tiles = dom_ids(page, ".camera-grid-tile", "cameraId", result)
    result["checks"]["camera_chips"] = len(chips)
    result["checks"]["camera_tiles"] = len(tiles)
    if set(expectations["camera_ids"]) - set(chips):
        result["issues"].append("Cameras missing under Ingress")
    if set(expectations["camera_ids"]) - set(tiles):
        result["issues"].append("Grid tiles missing under Ingress")

    api_reqs = [r for r in sink["requests"] if r["type"] in ("fetch", "eventsource", "xhr")]
    bad_api = [r for r in api_reqs if f"{INGRESS_PATH}/api/" not in r["url"]]
    result["checks"]["api_requests"] = len(api_reqs)
    result["checks"]["api_requests_outside_prefix"] = [r["url"] for r in bad_api[:10]]
    result["checks"]["event_source_request"] = any(
        r["type"] == "eventsource" and "/events/stream" in r["url"] for r in api_reqs
    )
    if bad_api:
        result["issues"].append(
            f"{len(bad_api)} API/SSE request(s) escaped the Ingress prefix: {[r['url'] for r in bad_api[:3]]}"
        )
    if not result["checks"]["event_source_request"]:
        result["warnings"].append("No SSE /events/stream request observed under Ingress")

    asset_responses: list[dict[str, str]] = []

    def on_asset(resp: Any) -> None:
        if "/static/" in resp.url:
            asset_responses.append({"url": resp.url[:250], "status": str(resp.status)})

    page.on("response", on_asset)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".camera-chip", timeout=45000)
    page.wait_for_timeout(500)
    page.remove_listener("response", on_asset)
    result["checks"]["asset_responses"] = asset_responses[:12]
    bad_assets = [a for a in asset_responses if a["status"] not in ("200", "304")]
    if not asset_responses:
        result["warnings"].append("No /static/ asset responses captured under Ingress")
    elif bad_assets:
        result["issues"].append(f"Static assets failing under Ingress: {bad_assets[:5]}")

    if sink["page_errors"]:
        result["issues"].append(f"JS exceptions under Ingress: {sink['page_errors'][:3]}")
    if sink["console_errors"]:
        result["issues"].append(f"Console errors under Ingress: {sink['console_errors'][:3]}")
    result["checks"]["console_errors"] = sink["console_errors"][:10]
    result["checks"]["bad_responses"] = sink["bad_responses"][:10]

    page.screenshot(path=str(SHOT_DIR / "ingress_standalone_view.png"), animations="disabled")

    # Embedded in a Home Assistant-like iframe on the same origin (CSP frame-ancestors 'self').
    host = (
        "<!DOCTYPE html><html><head><title>HA Ingress Host</title></head>"
        '<body style="margin:0;padding:0;background:#111">'
        f'<iframe id="ingress-iframe" src="{url}" width="100%" height="800" '
        'style="border:0;width:100vw;height:100vh"></iframe></body></html>'
    )
    page.set_content(host, wait_until="domcontentloaded")
    frame = page.frame_locator("#ingress-iframe")
    try:
        frame.locator(".brand-logo").wait_for(state="visible", timeout=30000)
        frame.locator(".camera-chip").first.wait_for(state="visible", timeout=30000)
        frame.locator(".event-card").first.wait_for(state="visible", timeout=30000)
        frame_chips = frame.locator(".camera-chip").count()
        frame_events = frame.locator(".event-card").count()
        result["checks"]["iframe"] = {
            "brand_visible": True,
            "camera_chips": frame_chips,
            "event_cards": frame_events,
        }
        if frame_chips < len(expectations["camera_ids"]):
            result["issues"].append(f"iframe shows only {frame_chips} camera chips")
        if frame_events == 0:
            result["issues"].append("iframe shows no event cards")
        page.screenshot(path=str(SHOT_DIR / "ingress_iframe.png"), animations="disabled")
    except Exception as exc:
        result["issues"].append(f"Ingress iframe embedding failed: {compact(str(exc))}")
        result["checks"]["iframe"] = {"error": compact(str(exc))}

    detach_diagnostics(page, sink)
    page.close()
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    global BASE_URL, INGRESS_TOKEN, INGRESS_PATH, OUT_DIR, SHOT_DIR, REPORT_PATH

    parser = argparse.ArgumentParser(
        description="Responsive/UI/Ingress audit of the Lookout viewer"
    )
    parser.add_argument("--base-url", default=BASE_URL, help="deployment URL to audit")
    parser.add_argument(
        "--out-dir", default=str(OUT_DIR), help="directory for report and screenshots"
    )
    parser.add_argument(
        "--ingress-token", default=INGRESS_TOKEN, help="HA Ingress token path segment"
    )
    args = parser.parse_args(argv)

    BASE_URL = args.base_url.rstrip("/")
    INGRESS_TOKEN = args.ingress_token
    INGRESS_PATH = f"/api/hassio_ingress/{INGRESS_TOKEN}"
    OUT_DIR = Path(args.out_dir)
    SHOT_DIR = OUT_DIR / "lookout-viewport-screenshots"
    REPORT_PATH = OUT_DIR / "lookout-viewport-report.json"

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "ingress_path": INGRESS_PATH,
        "resolved_via": RESOLVE_IP,
        "expectations": {},
        "viewports": [],
        "interactions": [],
        "focus_view": [],
        "ingress": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[f"--host-resolver-rules=MAP {BASE_URL.split('//')[1]} {RESOLVE_IP}"],
        )
        context = browser.new_context(
            ignore_https_errors=True, device_scale_factor=1, locale="en-US"
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        page.set_viewport_size({"width": 1920, "height": 1080})

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(".camera-chip", timeout=45000)
            expectations = fetch_expectations(page)
        except Exception as exc:
            print(f"FATAL: cannot load {BASE_URL}: {exc}", file=sys.stderr)
            browser.close()
            return 2
        report["expectations"] = expectations
        print(
            f"Ground truth: {len(expectations['camera_ids'])} enabled cameras, "
            f"{len(expectations['event_ids'])} events loaded (server total: {expectations['event_total']})\n"
        )

        print("=== PHASE 1: VIEWPORTS ===")
        for name, width, height, note in VIEWPORTS:
            print(f"[{name}] {width}x{height} ... ", end="", flush=True)
            result = audit_viewport(page, name, width, height, note, expectations)
            report["viewports"].append(result)
            status = "FAIL" if result["issues"] else ("OK" if not result["warnings"] else "OK*")
            print(f"{status} (issues={len(result['issues'])}, warnings={len(result['warnings'])})")
            for issue in result["issues"]:
                print(f"    - {issue[:400]}")
            for warning in result["warnings"][:4]:
                print(f"    ~ {warning[:300]}")
            if len(result["warnings"]) > 4:
                print(f"    ~ ... and {len(result['warnings']) - 4} more")

        print("\n=== PHASE 2: INTERACTIONS ===")
        for label, _width, _height in INTERACTION_VIEWPORTS:
            print(f"[{label}] ... ", end="", flush=True)
            result = run_interaction_suite(page, label, expectations)
            report["interactions"].append(result)
            failed = [s for s in result["steps"] if not s["ok"]]
            print(
                f"{'FAIL' if failed else 'OK'} ({len(result['steps']) - len(failed)}/{len(result['steps'])} steps passed)"
            )
            for step in failed:
                print(f"    - {step['name']}: {step['detail']}")
            for warning in result["warnings"][:3]:
                print(f"    ~ {warning[:300]}")

        print("\n=== PHASE 3: FOCUS VIEW (PIXEL 9 PRO FOLD) ===")
        for name, width, height, _note in FOCUS_VIEW_VIEWPORTS:
            print(f"[{name}] {width}x{height} ... ", end="", flush=True)
            result = audit_focus_view(page, name, width, height, expectations)
            report["focus_view"].append(result)
            failed = [c for c, ok in result["checks"].items() if not ok]
            print(
                f"{'FAIL' if failed else 'OK'} "
                f"({len(result['checks']) - len(failed)}/{len(result['checks'])} checks passed)"
            )
            for issue in result["issues"]:
                print(f"    - {issue[:400]}")
            for warning in result["warnings"][:3]:
                print(f"    ~ {warning[:300]}")

        print("\n=== PHASE 4: HOME ASSISTANT INGRESS ===")
        print(f"[{INGRESS_PATH}/] ... ", end="", flush=True)
        report["ingress"] = audit_ingress(context, expectations)
        ing = report["ingress"]
        print(
            f"{'FAIL' if ing['issues'] else 'OK'} (issues={len(ing['issues'])}, warnings={len(ing['warnings'])})"
        )
        for issue in ing["issues"]:
            print(f"    - {issue[:400]}")
        for warning in ing["warnings"]:
            print(f"    ~ {warning[:300]}")
        print(f"    base href: {ing['checks'].get('base_href')}")
        print(f"    API/SSE requests under prefix: {ing['checks'].get('api_requests')}")
        print(f"    iframe: {ing['checks'].get('iframe')}")

        browser.close()

    report["summary"] = {
        "viewports_total": len(report["viewports"]),
        "viewports_passing": sum(1 for v in report["viewports"] if not v["issues"]),
        "viewports_failing": sum(1 for v in report["viewports"] if v["issues"]),
        "interaction_steps_total": sum(len(i["steps"]) for i in report["interactions"]),
        "interaction_steps_failed": sum(
            sum(1 for s in i["steps"] if not s["ok"]) for i in report["interactions"]
        ),
        "ingress_issues": len(report["ingress"].get("issues", [])),
        "focus_view_total": len(report["focus_view"]),
        "focus_view_checks_total": sum(len(r["checks"]) for r in report["focus_view"]),
        "focus_view_checks_failed": sum(
            sum(1 for ok in r["checks"].values() if not ok) for r in report["focus_view"]
        ),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport: {REPORT_PATH}")
    print(f"Screenshots: {SHOT_DIR}")
    print(f"Summary: {json.dumps(report['summary'])}")
    return (
        0
        if (
            report["summary"]["viewports_failing"] == 0
            and report["summary"]["interaction_steps_failed"] == 0
            and report["summary"]["focus_view_checks_failed"] == 0
            and report["summary"]["ingress_issues"] == 0
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
