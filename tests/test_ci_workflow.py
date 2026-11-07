"""Unit tests for GitHub Actions CI workflow definition."""

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def workflow_path() -> Path:
    """Return path to the CI workflow file."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / ".github" / "workflows" / "ci.yml"


@pytest.fixture
def workflow_data(workflow_path: Path) -> dict[str, Any]:
    """Parse and return CI workflow YAML contents."""
    with open(workflow_path, encoding="utf-8") as f:
        data: Any = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def test_ci_workflow_file_exists(workflow_path: Path) -> None:
    """Verify that .github/workflows/ci.yml exists and is a file (TR-1)."""
    assert workflow_path.is_file()


def test_ci_workflow_valid_yaml(workflow_data: dict[str, Any]) -> None:
    """Verify that ci.yml contains valid YAML and parsed into a mapping (TR-2)."""
    assert isinstance(workflow_data, dict)
    assert workflow_data.get("name") == "CI"


def test_ci_workflow_trigger_events(workflow_data: dict[str, Any]) -> None:
    """Verify trigger conditions for push, pull_request, and workflow_dispatch (TR-3)."""
    triggers = workflow_data.get("on", {})
    assert "push" in triggers
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers

    assert "main" in workflow_data["on"]["push"]["branches"]
    assert "phase/**" in workflow_data["on"]["push"]["branches"]
    assert "main" in workflow_data["on"]["pull_request"]["branches"]
    assert "phase/**" in workflow_data["on"]["pull_request"]["branches"]


def test_ci_workflow_uv_caching_and_python_version(workflow_data: dict[str, Any]) -> None:
    """Verify setup-uv step with caching and Python 3.12 (TR-4)."""
    quality_job = workflow_data["jobs"]["quality"]
    steps = quality_job.get("steps", [])
    uv_step = next((s for s in steps if "setup-uv" in s.get("uses", "")), None)

    assert uv_step is not None
    assert uv_step.get("with", {}).get("enable-cache") is True
    assert str(uv_step.get("with", {}).get("python-version")) == "3.12"


def test_ci_workflow_quality_commands(workflow_data: dict[str, Any]) -> None:
    """Verify required quality commands (ruff, mypy, pytest) are defined (TR-5)."""
    quality_job = workflow_data["jobs"]["quality"]
    steps = quality_job.get("steps", [])

    assert any("ruff check" in step.get("run", "") for step in steps)
    assert any("ruff format --check" in step.get("run", "") for step in steps)
    assert any("mypy src" in step.get("run", "") for step in steps)
    assert any("pytest --cov" in step.get("run", "") for step in steps)
    assert any("uv sync" in step.get("run", "") for step in steps)


def test_ci_workflow_critique_review_job(workflow_data: dict[str, Any]) -> None:
    """Verify critique PR review job configuration, inputs, and permissions (TR-6)."""
    assert "critique" in workflow_data["jobs"]
    critique_job = workflow_data["jobs"]["critique"]

    # Verify PR conditional guard
    job_condition = critique_job.get("if", "")
    assert "pull_request" in job_condition
    assert "draft == false" in job_condition

    # Verify elevated permissions
    permissions = critique_job.get("permissions", {})
    assert permissions.get("pull-requests") == "write"
    assert permissions.get("issues") == "write"
    assert permissions.get("contents") == "write"

    # Verify critique step
    steps = critique_job.get("steps", [])
    checkout_step = next((s for s in steps if "checkout" in s.get("uses", "")), None)
    assert checkout_step is not None
    assert checkout_step.get("with", {}).get("fetch-depth") == 0

    critique_step = next((s for s in steps if "critique" in s.get("uses", "")), None)
    assert critique_step is not None
    inputs = critique_step.get("with", {})
    assert inputs.get("gemini_api_key") == "${{ secrets.GEMINI_API_KEY }}"
    assert inputs.get("github_token") == "${{ secrets.GITHUB_TOKEN }}"
    assert inputs.get("fail_on_severity") == "none"


def test_ci_workflow_concurrency_and_permissions(workflow_data: dict[str, Any]) -> None:
    """Verify top-level concurrency and least-privilege permissions (TR-7)."""
    assert workflow_data.get("concurrency", {}).get("cancel-in-progress") is True
    assert "${{ github.workflow }}" in workflow_data.get("concurrency", {}).get("group", "")

    top_permissions = workflow_data.get("permissions", {})
    assert top_permissions.get("contents") == "read"


def test_ci_workflow_container_and_ha_job(workflow_data: dict[str, Any]) -> None:
    """Verify container-and-ha testing job configuration, steps, and linters (TR-8)."""
    assert "container-and-ha" in workflow_data["jobs"]
    ha_job = workflow_data["jobs"]["container-and-ha"]
    assert ha_job.get("runs-on") == "ubuntu-latest"

    steps = ha_job.get("steps", [])
    assert any("docker compose config" in step.get("run", "") for step in steps)
    assert any(
        "docker compose -f docker-compose.sidecar.yml config" in step.get("run", "")
        for step in steps
    )
    assert any("docker build -t lookout:test" in step.get("run", "") for step in steps)

    linter_step = next((s for s in steps if "action-addon-linter" in s.get("uses", "")), None)
    assert linter_step is not None
    assert linter_step.get("with", {}).get("path") == "./ha-addon"

    assert any("check_config" in step.get("run", "") for step in steps)
    assert any("docker network create" in step.get("run", "") for step in steps)
    assert any("/api/v1/events/trigger" in step.get("run", "") for step in steps)
