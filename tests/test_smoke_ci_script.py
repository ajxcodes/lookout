"""Unit tests for local smoke runner script."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def smoke_script() -> Path:
    """Return path to scripts/smoke_ci.sh."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "scripts" / "smoke_ci.sh"


def test_smoke_ci_script_executable(smoke_script: Path) -> None:
    """Verify scripts/smoke_ci.sh exists and is executable (TR-2)."""
    assert smoke_script.is_file() and os.access(smoke_script, os.X_OK)


def test_smoke_ci_script_e2e_flag_supported(smoke_script: Path) -> None:
    """Verify scripts/smoke_ci.sh supports --e2e flag (TR-3)."""
    assert "--e2e" in smoke_script.read_text(encoding="utf-8")
