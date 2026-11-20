"""Tests for ntfy deep links sharing the HA sync deep-link base (AC14 / TR-17)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lookout.adapters.home_assistant_sync import HomeAssistantSyncManager
from lookout.adapters.web.app import _build_notification
from lookout.config.deep_links import get_deep_link_base
from lookout.config.schema import (
    HomeAssistantConfig,
    LookoutConfig,
    NotificationTargetConfig,
)
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


def _config(**kwargs: object) -> LookoutConfig:
    """Build a config with an enabled ntfy target plus the supplied overrides."""
    return LookoutConfig(
        notification_targets=[
            NotificationTargetConfig(name="ntfy", target_type="ntfy", destination="ntfy://lookout")
        ],
        **kwargs,  # type: ignore[arg-type]
    )


def test_ntfy_uses_shared_ingress_base() -> None:
    """TR-17: ntfy uses the same ingress base as the sync manager and helper."""
    cfg = _config(
        home_assistant=HomeAssistantConfig(
            ingress_url="https://ha.example.com/api/hassio_ingress/abc/"
        ),
        home_assistant_url="https://ha.example.com",
        public_url="https://lookout.example.com",
    )
    dispatcher, _, _ = _build_notification(cfg)
    manager = HomeAssistantSyncManager(config=cfg)

    assert dispatcher is not None
    expected = get_deep_link_base(cfg)
    assert expected == "https://ha.example.com/api/hassio_ingress/abc"
    assert manager.deep_link_base == expected
    assert dispatcher.deep_link_base_url == expected
    assert dispatcher.use_companion_app is True


def test_ntfy_base_falls_back_to_public_url_without_ha() -> None:
    """No HA configured: ntfy links point directly at public_url."""
    cfg = _config(public_url="https://lookout.example.com/")
    dispatcher, _, _ = _build_notification(cfg)
    assert dispatcher is not None
    assert dispatcher.deep_link_base_url == "https://lookout.example.com"
    assert dispatcher.use_companion_app is False


def test_ntfy_base_prefers_home_assistant_url_path() -> None:
    """home_assistant_url yields the shared /lookout/viewer panel path."""
    cfg = _config(
        home_assistant_url="https://ha.example.com/",
        public_url="https://lookout.example.com",
    )
    dispatcher, _, _ = _build_notification(cfg)
    assert dispatcher is not None
    assert dispatcher.deep_link_base_url == "https://ha.example.com/lookout/viewer"
    assert dispatcher.use_companion_app is True


@pytest.mark.asyncio
async def test_ntfy_notification_uses_viewer_query_parameters() -> None:
    """HA notifications use viewer query params (camera_id / live) on the shared base."""
    cfg = _config(home_assistant_url="https://ha.example.com")
    dispatcher, _, _ = _build_notification(cfg)
    assert dispatcher is not None

    event = CameraEvent(
        id="evt-1",
        camera_id="cam1",
        timestamp=datetime.now(UTC),
        category=EventCategory.UNKNOWN_VISITOR,
        threat_level=ThreatLevel.HIGH,
        summary="Person detected at cam1",
    )
    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200

    async def fake_post(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> FakeResponse:
        if headers:
            for key, value in headers.items():
                captured[key.lower()] = str(value)
        return FakeResponse()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=fake_post)
    dispatcher._client = mock_client

    ok = await dispatcher.send_notification(event=event, target="default")
    assert ok is True
    actions = captured["actions"]
    assert "lookout/viewer?camera_id=cam1" in actions
    assert "event_id=evt-1" in actions
    assert "live=true" in actions
    assert "/events/" not in captured.get("click", "")
