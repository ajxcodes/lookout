"""Standalone tests for NtfyDispatcher notification delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lookout.adapters.ntfy import NtfyDispatcher
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@pytest.mark.asyncio
async def test_ntfy_multi_object_and_threat_emojis_standalone() -> None:
    """Verify NtfyDispatcher sends multi-object and threat emojis with X-Sequence-ID."""
    dispatcher = NtfyDispatcher(topic="lookout-test", server="https://ntfy.sh")

    event = CameraEvent(
        id="evt-ntfy-1",
        camera_id="front_door",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.HIGH,
        summary="Person and package detected at front_door",
        matched_entities=["person", "package"],
    )

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

        def raise_for_status(self) -> None:
            return None

    async def fake_post(
        url: str,
        *,
        content: bytes | str = b"",
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> FakeResponse:
        if headers:
            for k, v in headers.items():
                captured[k.lower()] = str(v)
        return FakeResponse()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=fake_post)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await dispatcher.send_notification(event=event, target="default")

    assert ok is True
    assert captured.get("x-sequence-id") == event.id
