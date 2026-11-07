"""Lookout: Smart Home Camera Intelligence Platform."""

import json
from importlib import metadata
from pathlib import Path


def _get_version() -> str:
    for candidate in (
        Path(__file__).resolve().parent.parent.parent / "package.json",
        Path("package.json"),
    ):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if "version" in data and isinstance(data["version"], str):
                    return str(data["version"])
            except Exception:
                pass
    try:
        return metadata.version("lookout")
    except Exception:
        return "0.0.0"


__version__ = _get_version()
