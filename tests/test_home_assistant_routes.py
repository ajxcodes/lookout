"""Tests for Home Assistant routes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from auth_test_helpers import make_client

from lookout.config.schema import HomeAssistantConfig, LookoutConfig


@pytest.mark.asyncio
async def test_get_config_masks_token(tmp_path) -> None:
    config = LookoutConfig(home_assistant=HomeAssistantConfig(access_token="secret-token-value"))
    client, app, _ = make_client(tmp_path, config)
    resp = client.get("/api/home-assistant/config")
    assert resp.status_code == 200
    data = resp.json()
    # find access_token in response
    token_val = None
    if "access_token" in data:
        token_val = data["access_token"]
    elif "home_assistant" in data and isinstance(data["home_assistant"], dict):
        token_val = data["home_assistant"].get("access_token")
    assert token_val is not None
    assert token_val != "secret-token-value"


def test_test_connection_endpoint(tmp_path) -> None:
    config = LookoutConfig()
    client, app, _ = make_client(tmp_path, config)
    # patch manager test_connection
    mock_mgr = MagicMock()
    mock_mgr.test_connection.return_value = {"ok": True, "status": 200}
    app.state.ha_sync = mock_mgr
    resp = client.post("/api/home-assistant/test")
    assert resp.status_code == 200
    assert "ok" in resp.json()
    assert isinstance(resp.json()["ok"], bool)
