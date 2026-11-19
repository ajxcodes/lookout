"""AC11 / TR-24 — static Dockerfile checks ensuring the runtime image can run
real Ring authentication via the bundled ``ring-auth-cli`` on Node 20.
"""

from pathlib import Path

from lookout.providers.ring.authenticator import DEFAULT_CLI_PATH


def test_runtime_image_provisions_ring_auth_cli() -> None:
    """The image installs Node 20 + ring-client-api@14.3.0 and exposes the CLI."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    # A pinned Node 20 stage is declared (bookworm's apt nodejs v18 is too old).
    assert "FROM node:20-bookworm-slim AS ring-cli" in dockerfile

    # The exact package version is installed in that stage, not in the runtime.
    assert "npm install" in dockerfile
    assert "ring-client-api@14.3.0" in dockerfile

    # The CLI is asserted executable at build time (Node, executable, module).
    assert "node --version" in dockerfile
    assert "test -x" in dockerfile
    assert '''node -e "require('ring-client-api')"''' in dockerfile

    # The Node 20 binary and the installed dependency tree are copied into the
    # runtime stage, and the tree is owned by the non-root lookout user.
    assert "COPY --from=ring-cli /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert (
        "COPY --from=ring-cli --chown=lookout:lookout /opt/ring-cli/node_modules /app/node_modules"
        in dockerfile
    )

    # The CLI path is exactly the one the default RingAuthCliIssuer launches.
    assert DEFAULT_CLI_PATH == "/app/node_modules/.bin/ring-auth-cli"
    assert DEFAULT_CLI_PATH in dockerfile
