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


def test_ci_workflow_playwright_e2e_job(workflow_data: dict[str, Any]) -> None:
    """Verify playwright-e2e job configuration, steps, and artifact upload (TR-1)."""
    assert "playwright-e2e" in workflow_data["jobs"]
    e2e_job = workflow_data["jobs"]["playwright-e2e"]
    assert e2e_job.get("runs-on") == "ubuntu-latest"

    steps = e2e_job.get("steps", [])
    assert any("playwright install" in s.get("run", "") for s in steps)
    assert any("pytest tests/e2e" in s.get("run", "") for s in steps)

    upload_step = next((s for s in steps if "upload-artifact" in s.get("uses", "")), None)
    assert upload_step is not None
    assert upload_step.get("with", {}).get("name") == "playwright-test-results"


def test_ci_workflow_publish_job_defined(workflow_data: dict[str, Any]) -> None:
    """TR-1: Verify container-publish job is defined with packages: write permission."""
    assert "container-publish" in workflow_data["jobs"]
    publish_job = workflow_data["jobs"]["container-publish"]
    assert publish_job.get("permissions", {}).get("packages") == "write"
    assert "v*.*.*" in workflow_data["on"]["push"]["tags"]


def test_ci_workflow_publish_matrix_targets(workflow_data: dict[str, Any]) -> None:
    """TR-2: Verify container-publish matrix includes standalone and ha-addon targets."""
    assert {"standalone", "ha-addon"}.issubset(
        {
            t["target"]
            for t in workflow_data["jobs"]["container-publish"]["strategy"]["matrix"]["include"]
        }
    )


def test_ci_workflow_qemu_and_buildx_steps(workflow_data: dict[str, Any]) -> None:
    """TR-3: Verify QEMU and Buildx setup actions are configured."""
    steps = workflow_data["jobs"]["container-publish"]["steps"]
    assert any("docker/setup-qemu-action" in s.get("uses", "") for s in steps) and any(
        "docker/setup-buildx-action" in s.get("uses", "") for s in steps
    )


def test_ci_workflow_multiarch_platforms(workflow_data: dict[str, Any]) -> None:
    """TR-4: Verify multi-architecture platforms linux/amd64,linux/arm64 are declared."""
    steps = workflow_data["jobs"]["container-publish"]["steps"]
    assert any("linux/amd64,linux/arm64" in s.get("with", {}).get("platforms", "") for s in steps)


def test_ci_workflow_gha_cache_configuration(workflow_data: dict[str, Any]) -> None:
    """TR-5: Verify GitHub Actions BuildKit cache backend (type=gha) is configured."""
    steps = workflow_data["jobs"]["container-publish"]["steps"]
    assert any("type=gha" in s.get("with", {}).get("cache-from", "") for s in steps)


def test_ci_workflow_ghcr_and_dockerhub_logins(workflow_data: dict[str, Any]) -> None:
    """TR-6: Verify GHCR and conditional Docker Hub registry login steps."""
    steps = workflow_data["jobs"]["container-publish"]["steps"]
    assert any("ghcr.io" in s.get("with", {}).get("registry", "") for s in steps) and any(
        "DOCKERHUB_USERNAME" in s.get("with", {}).get("username", "") for s in steps
    )


def test_ci_workflow_conditional_push_logic(workflow_data: dict[str, Any]) -> None:
    """TR-7: Verify PR and phase branch builds keep push disabled."""
    steps = workflow_data["jobs"]["container-publish"]["steps"]
    assert any(
        "github.ref == 'refs/heads/main'" in s.get("with", {}).get("push", "") for s in steps
    )


def test_ci_workflow_auto_tag_job(workflow_data: dict[str, Any]) -> None:
    """TR-8: Verify auto-tag job runs ajxcodes/auto-tag on push to main."""
    assert "auto-tag" in workflow_data["jobs"] and any(
        "ajxcodes/auto-tag" in s.get("uses", "") for s in workflow_data["jobs"]["auto-tag"]["steps"]
    )


def test_ci_workflow_auto_tag_outputs_and_token(workflow_data: dict[str, Any]) -> None:
    """TR-14: Verify auto-tag exports new_tag output and passes GITHUB_TOKEN and package-json."""
    assert "new_tag" in workflow_data["jobs"]["auto-tag"]["outputs"] and any(
        "GITHUB_TOKEN" in s.get("with", {}).get("github-token", "")
        for s in workflow_data["jobs"]["auto-tag"]["steps"]
    )
    assert "release_url" in workflow_data["jobs"]["auto-tag"]["outputs"]
    assert "bumped" in workflow_data["jobs"]["auto-tag"]["outputs"]
    assert any(
        str(s.get("with", {}).get("package-json", "")).lower() == "true"
        for s in workflow_data["jobs"]["auto-tag"]["steps"]
    )


def test_ci_workflow_link_release_packages_job(workflow_data: dict[str, Any]) -> None:
    """TR-15: Verify link-release-packages job depends on auto-tag and container-publish."""
    assert "link-release-packages" in workflow_data["jobs"] and set(
        workflow_data["jobs"]["link-release-packages"]["needs"]
    ) == {"auto-tag", "container-publish"}
    link_job = workflow_data["jobs"]["link-release-packages"]
    assert link_job.get("permissions", {}).get("contents") == "write"
    assert any("gh release edit" in s.get("run", "") for s in link_job.get("steps", []))
