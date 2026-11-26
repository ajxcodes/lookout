#!/usr/bin/env python3
"""Keep generated version tokens in sync with ``package.json``.

``package.json`` is the single source of truth for the Lookout version.  Every
other version declaration (HA add-on config, HACS manifest, viewer cache
busters and the Dockerfile OCI-version fallbacks) is a generated token owned by
this script.

Usage::

    python3 scripts/sync_version.py             # write mode (default)
    python3 scripts/sync_version.py --check     # read-only drift guard
    python3 scripts/sync_version.py --root DIR  # operate on an alternate root
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

BEGIN_MARKER = "# BEGIN LOOKOUT VERSION FALLBACK (managed by scripts/sync_version.py)"
END_MARKER = "# END LOOKOUT VERSION FALLBACK"
ENV_PREFIX = 'ENV RELEASE_VERSION="${RELEASE_VERSION:-'
ENV_SUFFIX = '}"'


class ManagedTarget(NamedTuple):
    """A generated token (or set of tokens) in one tracked file."""

    relpath: str
    pattern: re.Pattern[str]
    expected_count: int
    render: Callable[[re.Match[str], str], str]


def _render(match: re.Match[str], version: str) -> str:
    """Replace only the captured ``value`` group, preserving its surroundings."""
    return match.group("prefix") + version + match.group("suffix")


def _target(relpath: str, pattern: str, expected_count: int, flags: int = 0) -> ManagedTarget:
    """Build a target from a regex with ``prefix``/``value``/``suffix`` groups."""
    return ManagedTarget(relpath, re.compile(pattern, flags), expected_count, _render)


def managed_targets() -> list[ManagedTarget]:
    """Return the ordered list of generated version tokens."""
    # HA add-on config.yaml: the leading ``version: "..."`` line.
    yaml_line = r"^(?P<prefix>" + re.escape('version: "') + r")(?P<value>[^\"]*)(?P<suffix>\")"
    # HACS custom component manifest.json: the single ``"version": "..."`` key.
    manifest_token = (
        r"(?P<prefix>" + re.escape('"version": "') + r")(?P<value>[^\"]*)(?P<suffix>\")"
    )
    # Viewer asset cache-busters: exactly three ``?v=...`` URL tokens.
    cache_buster = r"(?P<prefix>[?&]v=)(?P<value>[^\"'<>\s]+)(?P<suffix>)"
    # Dockerfile fallback: the ENV line inside the generated marker block.
    docker_block = (
        r"(?P<prefix>"
        + re.escape(BEGIN_MARKER)
        + r"\n"
        + re.escape(ENV_PREFIX)
        + r")(?P<value>[^\"]*)"
        + r"(?P<suffix>"
        + re.escape(ENV_SUFFIX)
        + r"\n"
        + re.escape(END_MARKER)
        + r")"
    )
    return [
        _target("ha-addon/config.yaml", yaml_line, 1, re.MULTILINE),
        _target("custom_components/lookout/manifest.json", manifest_token, 1),
        _target("src/lookout/adapters/web/static/index.html", cache_buster, 3),
        _target("Dockerfile", docker_block, 1),
        _target("ha-addon/Dockerfile", docker_block, 1),
    ]


def read_canonical(root: Path) -> str:
    """Read the canonical version from ``package.json`` at ``root``."""
    data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("package.json does not declare a non-empty string 'version'")
    return version


def sync_target(root: Path, target: ManagedTarget, version: str, check_only: bool) -> bool:
    """Synchronize (or check) one managed file. Return True when clean."""
    path = root / target.relpath
    text = path.read_text(encoding="utf-8")
    matches = list(target.pattern.finditer(text))
    if len(matches) != target.expected_count:
        raise ValueError(
            f"{target.relpath}: expected {target.expected_count} managed token(s), "
            f"found {len(matches)}"
        )
    found = [match.group("value") for match in matches]
    new_text = target.pattern.sub(lambda match: target.render(match, version), text)
    drifted = new_text != text

    if check_only:
        if drifted:
            print(f"DRIFT: {target.relpath} — expected {version}, found {', '.join(found)}")
            return False
        return True

    if drifted:
        path.write_text(new_text, encoding="utf-8")
    print(f"SYNCED {target.relpath}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="read-only: report drift and exit 1 instead of writing files",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="explicitly request write mode (the default when --check is absent)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="operate on an alternate repository root (defaults to this script's parent repo)",
    )
    args = parser.parse_args(argv)

    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    try:
        version = read_canonical(root)
        clean = True
        for target in managed_targets():
            if not sync_target(root, target, version, args.check):
                clean = False
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
