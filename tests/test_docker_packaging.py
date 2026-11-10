"""Unit tests validating Dockerfile directives, .dockerignore, and Docker Compose configurations."""

from pathlib import Path

import yaml


def test_dockerfile_multi_stage_structure() -> None:
    """TR-1: Verify Dockerfile defines multi-stage builder and runtime targets."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.is_file()
    content = dockerfile_path.read_text(encoding="utf-8")
    stages = [
        line.split("AS")[1].strip().lower()
        for line in content.splitlines()
        if line.strip().startswith("FROM") and "AS" in line
    ]
    assert "builder" in stages and "runtime" in stages


def test_dockerfile_non_root_user() -> None:
    """TR-2: Verify Dockerfile executes as non-root user lookout."""
    dockerfile_path = Path("Dockerfile")
    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")
    assert "USER lookout" in dockerfile_content


def test_dockerfile_ffmpeg_and_healthcheck() -> None:
    """TR-3: Verify Dockerfile installs FFmpeg and defines container HEALTHCHECK."""
    dockerfile_path = Path("Dockerfile")
    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")
    assert "ffmpeg" in dockerfile_content and "HEALTHCHECK" in dockerfile_content


def test_dockerignore_excludes_transient_dirs() -> None:
    """TR-4: Verify .dockerignore excludes repository metadata, virtualenvs, and artifacts."""
    dockerignore_path = Path(".dockerignore")
    assert dockerignore_path.is_file()
    dockerignore_patterns = [
        line.strip()
        for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all(pattern in dockerignore_patterns for pattern in [".git", ".venv", "artifacts"])


def test_docker_compose_standalone_validity() -> None:
    """TR-5: Verify standalone docker-compose.yml exposes port 8000 for zero-config launch."""
    compose_path = Path("docker-compose.yml")
    assert compose_path.is_file()
    with compose_path.open("r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)
    assert compose_data["services"]["lookout"]["ports"] == ["8000:8000"]


def test_docker_compose_sidecar_validity() -> None:
    """TR-6: Verify sidecar compose runs Lookout standalone (no external sidecars)."""
    sidecar_path = Path("docker-compose.sidecar.yml")
    assert sidecar_path.is_file()
    with sidecar_path.open("r", encoding="utf-8") as f:
        sidecar_data = yaml.safe_load(f)
    assert "lookout" in sidecar_data["services"]
    assert "healthcheck" in sidecar_data["services"]["lookout"]
    assert "wyze-bridge" not in sidecar_data["services"]
    assert "ring-mqtt" not in sidecar_data["services"]
    assert "go2rtc" not in sidecar_data["services"]


def test_docker_compose_sidecar_env_files() -> None:
    """TR-16: Native mode requires no sidecar env_file mounts."""
    sidecar_path = Path("docker-compose.sidecar.yml")
    assert sidecar_path.is_file()
    with sidecar_path.open("r", encoding="utf-8") as f:
        sidecar_data = yaml.safe_load(f)
    services = sidecar_data.get("services", {})
    for name in ("wyze-bridge", "ring-mqtt", "go2rtc"):
        assert name not in services
