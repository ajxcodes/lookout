"""Unit and integration tests for Home Assistant Add-On, templates, blueprints,
and ingress routes.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app


class _BlueprintYamlLoader(yaml.SafeLoader):
    """Custom YAML loader supporting Home Assistant blueprint !input tags."""

    pass


_BlueprintYamlLoader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


def test_ha_addon_config_ingress_enabled() -> None:
    """TR-7: Verify Home Assistant add-on config enables native Ingress on port 8000."""
    config_path = Path("ha-addon/config.yaml")
    assert config_path.is_file()
    with config_path.open("r", encoding="utf-8") as f:
        addon_config: dict[str, Any] = yaml.safe_load(f)
    assert addon_config["ingress"] is True and addon_config["ingress_port"] == 8000
    assert "boot" not in addon_config
    assert "homeassistant_config:rw" in addon_config.get("map", [])


def test_ha_addon_repository_metadata() -> None:
    """TR-8: Verify repository.yaml specifies expected Add-on repository metadata."""
    repo_path = Path("repository.yaml")
    assert repo_path.is_file()
    with repo_path.open("r", encoding="utf-8") as f:
        addon_repo: dict[str, Any] = yaml.safe_load(f)
    assert addon_repo["name"] == "Lookout Add-on Repository"


def test_ha_lovelace_card_template_validity() -> None:
    """TR-9: Verify example Lovelace card configuration template specifies Lookout viewer."""
    card_path = Path("templates/home_assistant_lovelace.yaml")
    assert card_path.is_file()
    with card_path.open("r", encoding="utf-8") as f:
        lovelace_card: dict[str, Any] = yaml.safe_load(f)
    assert "lookout" in lovelace_card["url"] or "type" in lovelace_card


def test_ha_motion_blueprint_validity() -> None:
    """TR-10: Verify Home Assistant camera motion trigger automation blueprint metadata."""
    blueprint_path = Path("templates/home_assistant_motion_blueprint.yaml")
    assert blueprint_path.is_file()
    with blueprint_path.open("r", encoding="utf-8") as f:
        blueprint_data: dict[str, Any] = yaml.load(f, Loader=_BlueprintYamlLoader)
    assert "blueprint" in blueprint_data and "domain" in blueprint_data["blueprint"]


def test_ha_panel_ingress_template_validity() -> None:
    """TR-11: Verify Home Assistant panel_iframe template specifies Lookout ingress panel."""
    panel_path = Path("templates/home_assistant_panel_ingress.yaml")
    assert panel_path.is_file()
    with panel_path.open("r", encoding="utf-8") as f:
        panel_data: dict[str, Any] = yaml.safe_load(f)
    assert "panel_iframe" in panel_data and "lookout" in panel_data["panel_iframe"]


def test_web_app_health_endpoint() -> None:
    """TR-12: Verify GET /health endpoint returns HTTP 200 with ok status."""
    app = create_app(repository=InMemoryEventRepository())
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_web_app_events_trigger_endpoint() -> None:
    """TR-13: Verify POST /api/v1/events/trigger ingests events and returns ok status."""
    app = create_app(repository=InMemoryEventRepository())
    client = TestClient(app)
    response = client.post("/api/v1/events/trigger", json={"camera_id": "front_door"})
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_documentation_and_architecture_guide_exist() -> None:
    """TR-14: Verify comprehensive README.md and docs/ARCHITECTURE.md exist."""
    readme_path = Path("README.md")
    arch_path = Path("docs/ARCHITECTURE.md")
    assert readme_path.is_file() and arch_path.is_file()


def test_targeted_notifications_with_entity_names() -> None:
    """TR-24: Verify targeted notification message incorporates entity names and zone."""
    from lookout.adapters.home_assistant import HomeAssistantEgressAdapter
    from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel

    adapter = HomeAssistantEgressAdapter(base_url="http://localhost:8123")
    event = CameraEvent(
        id="evt-pet-1",
        camera_id="backyard",
        timestamp=datetime.now(UTC),
        category=EventCategory.PET_ELIMINATION,
        threat_level=ThreatLevel.INFO,
        summary="Dog elimination",
        zone_id="lawn",
        matched_entities=["Mochi"],
    )
    payload = adapter._payload(event, message=None)
    assert "Mochi pooped in Backyard (Lawn)" in payload["message"]
