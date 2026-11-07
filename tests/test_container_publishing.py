"""Unit tests validating multi-arch container publishing, HA add-on metadata, and smoke runner."""

import json
from pathlib import Path
from typing import Any

import yaml


def test_ha_addon_config_image_reference() -> None:
    """TR-9: Verify Home Assistant add-on config declares GHCR prebuilt image and 64-bit archs."""
    addon_config: dict[str, Any] = yaml.safe_load(
        Path("ha-addon/config.yaml").read_text(encoding="utf-8")
    )
    assert addon_config.get("image") == "ghcr.io/ajxcodes/lookout-ha-addon" and set(
        addon_config.get("arch", [])
    ) == {"amd64", "aarch64"}


def test_ha_addon_build_yaml_architectures() -> None:
    """TR-10: Verify ha-addon/build.yaml maps amd64 and aarch64 to python:3.12-slim-bookworm."""
    build_config: dict[str, Any] = yaml.safe_load(
        Path("ha-addon/build.yaml").read_text(encoding="utf-8")
    )
    assert (
        build_config["build_from"]["amd64"] == "python:3.12-slim-bookworm"
        and build_config["build_from"]["aarch64"] == "python:3.12-slim-bookworm"
    )


def test_ha_addon_dockerfile_default_build_from() -> None:
    """TR-11: Verify ha-addon/Dockerfile sets default ARG BUILD_FROM."""
    assert "ARG BUILD_FROM=python:3.12-slim-bookworm" in Path("ha-addon/Dockerfile").read_text(
        encoding="utf-8"
    )


def test_smoke_ci_script_multiarch_flag_supported() -> None:
    """TR-12: Verify scripts/smoke_ci.sh supports the --multiarch validation flag."""
    assert "--multiarch" in Path("scripts/smoke_ci.sh").read_text(encoding="utf-8")


def test_package_json_version_anchor() -> None:
    """TR-13: Verify root package.json serves as canonical version anchor."""
    pkg_data: dict[str, Any] = json.loads(Path("package.json").read_text(encoding="utf-8"))
    assert (
        pkg_data.get("name") == "lookout"
        and pkg_data.get("private") is True
        and "version" in pkg_data
    )


def test_dockerfiles_declare_oci_source_and_version_labels() -> None:
    """Verify both Dockerfile and ha-addon/Dockerfile declare RELEASE_VERSION and OCI labels."""
    for df_path in (Path("Dockerfile"), Path("ha-addon/Dockerfile")):
        content = df_path.read_text(encoding="utf-8")
        assert "ARG RELEASE_VERSION=0.1.0" in content
        assert (
            'LABEL org.opencontainers.image.source="https://github.com/ajxcodes/lookout"' in content
        )
        assert 'LABEL org.opencontainers.image.version="${RELEASE_VERSION}"' in content


def test_smoke_ci_script_multiarch_docker_and_podman_support() -> None:
    """Verify smoke_ci.sh --multiarch supports both docker buildx and podman manifest builds."""
    script_text = Path("scripts/smoke_ci.sh").read_text(encoding="utf-8")
    assert "docker buildx build" in script_text
    assert "podman build --platform" in script_text
    assert "podman manifest inspect" in script_text


def test_publish_matrix_target_mappings_and_metadata_tags() -> None:
    """Verify matrix image mappings, graceful Docker Hub skip, and metadata tag patterns."""
    workflow_data: dict[str, Any] = yaml.safe_load(
        Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    publish_job = workflow_data["jobs"]["container-publish"]
    assert "auto-tag" in publish_job["needs"]
    matrix_items = {item["target"]: item for item in publish_job["strategy"]["matrix"]["include"]}

    standalone = matrix_items["standalone"]
    assert standalone["file"] == "Dockerfile"
    assert standalone["ghcr_image"] == "ghcr.io/ajxcodes/lookout"
    assert standalone["dockerhub_image"] == "ajxcodes/lookout"

    ha_addon = matrix_items["ha-addon"]
    assert ha_addon["file"] == "ha-addon/Dockerfile"
    assert ha_addon["ghcr_image"] == "ghcr.io/ajxcodes/lookout-ha-addon"
    assert ha_addon["dockerhub_image"] == "ajxcodes/lookout-ha-addon"
    assert "BUILD_FROM=python:3.12-slim-bookworm" in ha_addon["build_args"]

    steps = publish_job["steps"]
    dockerhub_login = next(
        s for s in steps if "DOCKERHUB_USERNAME" in s.get("with", {}).get("username", "")
    )
    assert "steps.dockerhub-check.outputs.enabled == 'true'" in dockerhub_login.get("if", "")

    meta_step = next(s for s in steps if "docker/metadata-action" in s.get("uses", ""))
    tags_cfg = meta_step["with"]["tags"]
    assert "value=latest" in tags_cfg
    assert "type=sha,prefix=sha-" in tags_cfg
    assert "pattern={{raw}}" in tags_cfg
    assert "pattern={{version}}" in tags_cfg
    assert "pattern={{major}}.{{minor}}" in tags_cfg
    assert "needs.auto-tag.outputs.new_tag" in tags_cfg


def test_readme_documents_container_registries_and_multiarch_smoke() -> None:
    """Verify README.md documents GHCR/Docker Hub container images and --multiarch smoke flag."""
    readme_text = Path("README.md").read_text(encoding="utf-8")
    assert "ghcr.io/ajxcodes/lookout" in readme_text
    assert "ghcr.io/ajxcodes/lookout-ha-addon" in readme_text
    assert "ajxcodes/lookout" in readme_text
    assert "ajxcodes/lookout-ha-addon" in readme_text
    assert "./scripts/smoke_ci.sh --multiarch" in readme_text
    assert "./scripts/smoke_ci.sh --e2e" in readme_text
