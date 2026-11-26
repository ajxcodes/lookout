"""Deterministic tests asserting package.json is the single source of truth."""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

import lookout
from lookout.adapters.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_version.py"
MANAGED_FILES = (
    "ha-addon/config.yaml",
    "custom_components/lookout/manifest.json",
    "src/lookout/adapters/web/static/index.html",
    "Dockerfile",
    "ha-addon/Dockerfile",
)


def _canonical() -> str:
    data = json.loads(Path("package.json").read_text(encoding="utf-8"))
    return str(data["version"])


def test_canonical_version_is_target_release() -> None:
    """TR-10: prove the target v0.7.0 bump landed in package.json."""
    assert json.loads(Path("package.json").read_text(encoding="utf-8"))["version"] == "0.7.0"


def test_all_generated_consumers_match_package_json() -> None:
    """TR-11: every generated token equals the canonical version; no 0.3.0 residue."""
    canonical = _canonical()

    addon_config = yaml.safe_load(Path("ha-addon/config.yaml").read_text(encoding="utf-8"))
    assert addon_config["version"] == canonical

    manifest = json.loads(
        Path("custom_components/lookout/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == canonical

    html = Path("src/lookout/adapters/web/static/index.html").read_text(encoding="utf-8")
    tokens = re.findall(r"[?&]v=([^\"'<>\s]+)", html)
    assert tokens and set(tokens) == {canonical}

    for df_path in (Path("Dockerfile"), Path("ha-addon/Dockerfile")):
        text = df_path.read_text(encoding="utf-8")
        env_lines = re.findall(
            r'^ENV RELEASE_VERSION="\$\{RELEASE_VERSION:-([^"]*)\}"$', text, re.MULTILINE
        )
        assert env_lines == [canonical], df_path

    for relpath in MANAGED_FILES:
        assert "0.3.0" not in Path(relpath).read_text(encoding="utf-8"), relpath


def test_runtime_and_http_version_surfaces_match_package_json() -> None:
    """TR-12: runtime, FastAPI/OpenAPI and /health all report the canonical version."""
    canonical = _canonical()
    app = create_app()
    assert lookout.__version__ == canonical
    assert app.version == canonical
    assert app.openapi()["info"]["version"] == canonical

    health = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    app.state.is_configured = True
    payload = asyncio.run(health.endpoint())
    assert payload == {"status": "ok", "configured": True, "version": canonical}


def test_sync_script_check_detects_and_repairs_drift(tmp_path: Path) -> None:
    """TR-13: --check detects drift; --write repairs it and is byte-idempotent."""
    for relpath in ("package.json",) + MANAGED_FILES:
        destination = tmp_path / relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / relpath).read_bytes())

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), *args, "--root", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )

    assert run("--check").returncode == 0

    manifest_path = tmp_path / "custom_components/lookout/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "9.9.9"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    drift = run("--check")
    assert drift.returncode != 0
    assert "DRIFT" in drift.stdout and "custom_components/lookout/manifest.json" in drift.stdout

    assert run("--write").returncode == 0
    assert run("--check").returncode == 0

    before = {relpath: (tmp_path / relpath).read_bytes() for relpath in MANAGED_FILES}
    assert run("--write").returncode == 0
    after = {relpath: (tmp_path / relpath).read_bytes() for relpath in MANAGED_FILES}
    assert before == after


def test_ci_enforces_version_drift_guard_and_release_writeback() -> None:
    """TR-14: CI checks drift, reconciles generated files, and guards release tags."""
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))

    quality_steps = workflow["jobs"]["quality"]["steps"]
    assert any(
        "python3 scripts/sync_version.py --check" in step.get("run", "") for step in quality_steps
    )

    auto_steps = workflow["jobs"]["auto-tag"]["steps"]
    assert any(
        str(step.get("with", {}).get("package-json", "")).lower() == "true" for step in auto_steps
    )
    assert any("scripts/sync_version.py --write" in step.get("run", "") for step in auto_steps)

    publish_steps = workflow["jobs"]["container-publish"]["steps"]
    assert any(
        "GITHUB_REF_NAME" in step.get("run", "")
        and "v$(jq -r .version package.json)" in step.get("run", "")
        for step in publish_steps
    )
