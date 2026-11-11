"""Tests for native credential routes: persistence, validation, and real connection tests."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig


class FakeNativeAdapter:
    def __init__(self, reachable: bool, error: str | None = None) -> None:
        self.reachable = reachable
        self.error = error
        self.calls = 0

    async def test_connection(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "reachable": self.reachable,
            "provider": "fake",
            "devices": [{"id": "dev-1"}] if self.reachable else [],
            "error": self.error,
        }

    async def discover_devices(self) -> list[dict[str, Any]]:
        return [{"id": "dev-1", "native_provider": "fake"}]


def _client(tmp_path: Path) -> tuple[TestClient, Any]:
    config_path = tmp_path / "nested" / "config.yaml"
    app = create_app(
        config=LookoutConfig(),
        repository=InMemoryEventRepository(),
        config_path=config_path,
    )
    return TestClient(app), app


def test_credentials_create_persists_real_config_file(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.post(
        "/api/native/credentials",
        json={
            "provider": "wyze",
            "credentials": {"auth_token": "access-token", "refresh_token": "refresh-token"},
        },
    )

    assert response.status_code == 200
    written = yaml.safe_load((tmp_path / "nested" / "config.yaml").read_text())
    assert written["wyze"]["auth_token"] == "access-token"
    assert written["wyze"]["refresh_token"] == "refresh-token"


def test_credentials_reject_invalid_values(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.post(
        "/api/native/credentials",
        json={"provider": "wyze", "credentials": {"not_a_real_field": "x"}},
    )

    assert response.status_code == 400
    assert not (tmp_path / "nested" / "config.yaml").exists()


def test_credentials_update_live_adapter_config_in_place(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    ring_config = app.state.ring_native.config
    wyze_config = app.state.wyze_native.config

    response = client.post(
        "/api/native/credentials",
        json={"ring": {"refresh_token": "new-ring-refresh"}},
    )

    assert response.status_code == 200
    # The adapters keep the same config object, so credentials reach them live.
    assert app.state.ring_native.config is ring_config
    assert app.state.config.ring is ring_config
    assert ring_config.refresh_token == "new-ring-refresh"
    written = yaml.safe_load((tmp_path / "nested" / "config.yaml").read_text())
    assert written["ring"]["refresh_token"] == "new-ring-refresh"

    response = client.post(
        "/api/native/credentials",
        json={"provider": "wyze", "credentials": {"refresh_token": "new-wyze-refresh"}},
    )

    assert response.status_code == 200
    assert app.state.wyze_native.config is wyze_config
    assert app.state.config.wyze is wyze_config
    assert wyze_config.refresh_token == "new-wyze-refresh"
    written = yaml.safe_load((tmp_path / "nested" / "config.yaml").read_text())
    assert written["wyze"]["refresh_token"] == "new-wyze-refresh"


def test_credentials_reject_invalid_nested_block(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    ring_config = app.state.ring_native.config

    response = client.post(
        "/api/native/credentials",
        json={"ring": {"refresh_token": "changed", "not_a_real_field": "x"}},
    )

    assert response.status_code == 400
    # Strict validation happens before any field is copied onto the live object.
    assert ring_config.refresh_token is None
    assert not (tmp_path / "nested" / "config.yaml").exists()


def test_persisted_config_is_owner_only(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file permissions only")

    client, _app = _client(tmp_path)
    response = client.post(
        "/api/native/credentials",
        json={"provider": "ring", "credentials": {"refresh_token": "refresh-token"}},
    )

    assert response.status_code == 200
    path = tmp_path / "nested" / "config.yaml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_credentials_test_uses_adapter_result_not_hardcoded_true(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    app.state.wyze_native = FakeNativeAdapter(reachable=False, error="401 Unauthorized")

    response = client.post("/api/native/credentials/test", json={"provider": "wyze"})

    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is False
    assert body["status"] == "error"
    assert body["error"] == "401 Unauthorized"
    assert app.state.wyze_native.calls == 1


def test_test_connection_checks_both_providers(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    app.state.wyze_native = FakeNativeAdapter(reachable=True)
    app.state.ring_native = FakeNativeAdapter(reachable=False, error="expired")

    response = client.post("/api/native/test-connection", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["wyze"]["reachable"] is True
    assert body["ring"]["reachable"] is False
    assert body["ring"]["error"] == "expired"


def test_alias_routes_call_real_handlers(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.post(
        "/api/providers/wyze/native/credentials",
        json={"credentials": {"auth_token": "alias-token"}},
    )

    assert response.status_code == 200
    written = yaml.safe_load((tmp_path / "nested" / "config.yaml").read_text())
    assert written["wyze"]["auth_token"] == "alias-token"


def test_discover_returns_real_provider_devices(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    app.state.ring_native = FakeNativeAdapter(reachable=True)

    response = client.post("/api/native/discover", json={"provider": "ring"})

    assert response.status_code == 200
    assert response.json()["devices"] == [{"id": "dev-1", "native_provider": "fake"}]


def test_discover_rejects_unknown_provider(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.post("/api/native/discover", json={"provider": "nope"})

    assert response.status_code == 400


def test_status_reports_configuration_without_network_io(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.get("/api/native/status")

    assert response.status_code == 200
    body = response.json()
    assert body["wyze_native_ingestion"] is False
    assert body["ring_native_ingestion"] is False
    assert body["wyze_configured"] is False
    assert body["ring_configured"] is False
    assert body["stream_manager"] is not None
    assert body["stream_manager"]["running"] is False
