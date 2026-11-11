"""Playwright E2E browser tests for Standalone Lookout deployment."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_standalone_onboarding_wizard_redirect_and_finalization(
    page: Page, unconfigured_server: dict[str, Any], tmp_path: Path
) -> None:
    """TR-7: Verify unconfigured app redirects to /setup, completes wizard, and launches viewer."""
    base_url = unconfigured_server["standalone_base_url"]
    page.goto(base_url)

    # Automatically redirected to /setup with step 1 visible
    expect(page.locator("#step-1")).to_be_visible()
    assert "/setup" in page.url and page.locator("#step-1").is_visible()

    # Step 1: AI Inference Provider
    page.locator("#ai-provider").select_option("local_edge")
    page.locator("#btn-test-ai").click()
    expect(page.locator("#ai-status-pill.success")).to_be_visible(timeout=5000)
    page.locator("#btn-next").click()

    # Step 2: Camera Feed Ingestion
    expect(page.locator("#step-2")).to_be_visible()
    page.locator("#cam-id").fill("porch-cam")
    page.locator("#cam-name").fill("Porch Camera")
    page.locator("#cam-url").fill("rtsp://localhost:8554/test_porch")
    page.locator("#btn-test-cam").click()
    expect(page.locator("#cam-status-pill.success")).to_be_visible(timeout=5000)
    page.locator("#btn-add-cam").click()
    page.locator("#btn-next").click()

    # Step 3: Household & Face Gallery
    expect(page.locator("#step-3")).to_be_visible()
    page.locator("#resident-name").fill("Alice")
    page.locator("#resident-id").fill("alice")

    dummy_face = tmp_path / "alice.jpg"
    dummy_face.write_bytes(b"dummy-face-bytes")
    page.set_input_files("#setup-face-file", str(dummy_face))
    expect(page.locator("#face-upload-status")).to_contain_text("Uploaded", timeout=5000)
    page.locator("#btn-next").click()

    # Step 4: Notifications & Webhooks
    expect(page.locator("#step-4")).to_be_visible()
    page.locator("#btn-test-notif").click()
    expect(page.locator("#notif-status-pill.success")).to_be_visible(timeout=5000)
    page.locator("#btn-next").click()

    # Step 5: Review & Finalize
    expect(page.locator("#step-5")).to_be_visible()
    expect(page.locator("#yaml-preview")).to_be_visible()
    page.locator("#btn-finalize").click()

    # Verify transition back to main viewer
    page.wait_for_url(f"{base_url}/", timeout=10000)
    expect(page.locator(".brand-logo")).to_be_visible()


def test_standalone_dashboard_camera_switcher_and_timeline(
    page: Page, standalone_base_url: str
) -> None:
    """TR-8: Verify camera switcher chips, badges, multi-selection, and timeline."""
    page.goto(standalone_base_url)

    expect(page.locator(".brand-logo")).to_be_visible()
    expect(page.locator(".camera-chip").first).to_be_visible()
    expect(page.locator(".event-card").first).to_be_visible()

    # In Grid mode, timeline shows all events
    expect(page.locator(".event-card")).to_have_count(2)

    # Camera chips show provider badges (Ring, Wyze, Generic)
    expect(page.locator(".badge-provider").first).to_be_visible()
    badge_texts = page.locator(".badge-provider").all_text_contents()
    assert any("Generic" in t for t in badge_texts)
    assert any("Ring" in t for t in badge_texts)

    # Multi-selection filters grid tiles
    expect(page.locator(".camera-grid-tile")).to_have_count(2)
    # Clicking a camera chip in grid mode toggles its selection off
    page.locator(".camera-chip").first.click()
    expect(page.locator(".camera-grid-tile")).to_have_count(1)
    # Clicking it again toggles it back on
    page.locator(".camera-chip").first.click()
    expect(page.locator(".camera-grid-tile")).to_have_count(2)


def test_standalone_event_playback_and_canvas(page: Page, standalone_base_url: str) -> None:
    """TR-9: Verify event card click loads video player, tag, and visualizer."""
    page.goto(standalone_base_url)

    expect(page.locator(".event-card").first).to_be_visible()
    # Ensure .event-camera-tag is rendered
    expect(page.locator(".event-camera-tag").first).to_be_visible()

    # Click first event card to play
    page.locator(".event-card").first.click()

    expect(page.locator("#event-video")).to_be_visible()
    expect(page.locator("#acoustic-canvas")).to_be_visible()
    expect(page.locator("#focus-ai-details-card")).to_be_visible()

    assert (
        page.locator("#event-video").is_visible()
        and page.locator("#acoustic-canvas").is_visible()
        and page.locator(".event-camera-tag").first.is_visible()
    )


def test_standalone_sse_realtime_event_push(page: Page, standalone_base_url: str) -> None:
    """TR-10: Verify real-time SSE stream prepends newly ingested event dynamically."""
    page.goto(standalone_base_url)
    expect(page.locator(".event-card").first).to_be_visible()

    # Broadcast event via trigger API
    with httpx.Client() as client:
        res = client.post(
            f"{standalone_base_url}/api/v1/events/trigger",
            json={
                "camera_id": "front-porch",
                "summary": "Realtime live test event push",
            },
        )
        assert res.status_code == 200
        live_event_id = res.json()["event_id"]

    # Verify new event card appears dynamically without full page reload
    event_locator = page.locator(f'[data-event-id="{live_event_id}"]')
    expect(event_locator).to_be_visible(timeout=8000)
    assert page.locator(f'[data-event-id="{live_event_id}"]').is_visible()


def test_standalone_resident_face_enrollment_modal(
    page: Page, standalone_base_url: str, tmp_path: Path
) -> None:
    """TR-11: Verify settings modal opens, lists faces, and allows new face photo enrollment."""
    page.goto(standalone_base_url)

    page.locator("#open-settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible()

    # Seed face photo upload
    test_face = tmp_path / "enrolled_face.jpg"
    test_face.write_bytes(b"mock-face-bytes")
    page.set_input_files("#face-file-input", str(test_face))

    expect(page.locator(".face-card").first).to_be_visible(timeout=5000)
    assert page.locator("#settings-modal").is_visible() and page.locator(".face-card").count() >= 1


def test_standalone_sidecar_2fa_modal_interaction(
    page: Page, unconfigured_server: dict[str, Any]
) -> None:
    """TR-17: Verify interactive 2FA modal opens and verification button is visible."""
    base_url = unconfigured_server["standalone_base_url"]
    page.goto(f"{base_url}/setup")

    expect(page.locator("#step-1")).to_be_visible()
    page.locator("#btn-next").click()

    # Step 2: Cameras & Sidecars
    expect(page.locator("#step-2")).to_be_visible()
    page.locator("#btn-auth-wyze").click()

    expect(page.locator("#modal-2fa")).to_be_visible(timeout=5000)
    expect(page.locator("#btn-verify-2fa")).to_be_visible(timeout=5000)
    assert page.locator("#modal-2fa").is_visible() and page.locator("#btn-verify-2fa").is_visible()


def test_standalone_ptz_controls_gated_on_ptz_enabled(page: Page, standalone_base_url: str) -> None:
    """Verify PTZ controls render only for ptz_enabled cameras."""
    page.goto(standalone_base_url)

    expect(page.locator(".brand-logo")).to_be_visible()

    # Backyard camera is ptz_enabled -> its tile should expose PTZ controls
    backyard_tile = page.locator('.camera-grid-tile[data-camera-id="backyard"]')
    expect(backyard_tile).to_be_visible()
    # PTZ d-pad buttons present (stop button uses stop symbol)
    expect(backyard_tile.locator("button", has_text="⏹")).to_be_visible()

    # Front-porch camera is NOT ptz_enabled -> no PTZ stop button
    front_tile = page.locator('.camera-grid-tile[data-camera-id="front-porch"]')
    expect(front_tile).to_be_visible()
    expect(front_tile.locator("button", has_text="⏹")).to_have_count(0)
