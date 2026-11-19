"""Real Ring token issuance via the bundled, non-interactive Node helper.

``RingAuthCliIssuer`` delegates the OAuth/2FA exchange to a small ESM helper
built on ``ring-client-api``.  It is the programmatic equivalent of
``ring-auth-cli`` (the exact same ``RingRestClient`` auth flow the interactive
CLI wraps internally) but uses no PTY, prints no prompts, and scrapes no output.
Credentials reach the helper only through a tightly scoped environment (the
three Ring values plus a minimal ``PATH``); no email, password, code, or token
is ever logged or written to a file.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Kept as a compatibility alias: the runtime image still provisions the CLI and
# external checks pin this path.  Issuance itself now uses the helper below.
DEFAULT_CLI_PATH = "/app/node_modules/.bin/ring-auth-cli"
DEFAULT_HELPER_PATH = "/app/src/lookout/adapters/ring_auth_helper.mjs"
DEFAULT_NODE_PATH = "/usr/local/bin/node"

_DEFAULT_CWD = "/app"
_HELPER_PATH_ENV = "/usr/local/bin:/usr/bin:/bin"
_EMAIL_ENV = "LOOKOUT_RING_EMAIL"
_PASSWORD_ENV = "LOOKOUT_RING_PASSWORD"
_CODE_ENV = "LOOKOUT_RING_2FA_CODE"
_MIN_TOKEN_LENGTH = 16
_MAX_TOKEN_LENGTH = 8192


def _auth_error(message: str) -> RuntimeError:
    """Build a Ring auth error without importing adapters at module load time."""
    from lookout.adapters.ring_native import RingAuthError

    return RingAuthError(message)


@runtime_checkable
class RingTokenIssuer(Protocol):
    """Injectable boundary for issuing a real RINGTOKEN."""

    def begin_login(self, email: str, password: str) -> str: ...
    def complete(self, session_id: str, code: str) -> str: ...
    def dispose(self, session_id: str) -> None: ...


@dataclass
class _HelperSession:
    email: str
    password: str


class RingAuthCliIssuer:
    """Issue a real RINGTOKEN by running the bundled Node auth helper."""

    def __init__(
        self,
        helper_path: str = DEFAULT_HELPER_PATH,
        *,
        node_path: str = DEFAULT_NODE_PATH,
        timeout: float = 120.0,
        cwd: str = _DEFAULT_CWD,
    ) -> None:
        self.helper_path = helper_path
        self.node_path = node_path
        self.timeout = float(timeout)
        self.cwd = cwd
        self._sessions: dict[str, _HelperSession] = {}

    def begin_login(self, email: str, password: str) -> str:
        """Validate the helper runtime, then hold credentials only in memory."""
        if not Path(self.helper_path).exists():
            raise _auth_error(
                "Ring auth helper is not available; real Ring login requires the "
                "bundled Node helper in the runtime image."
            )
        if not Path(self.node_path).exists():
            raise _auth_error(
                "Node runtime is not available; real Ring login requires Node 20 "
                "in the runtime image."
            )
        session_id = f"ring_issuer_{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = _HelperSession(email=email, password=password)
        return session_id

    def complete(self, session_id: str, code: str) -> str:
        """Run the helper for this 2FA code and return the raw RINGTOKEN."""
        session = self._sessions.get(session_id)
        if session is None:
            raise _auth_error("Invalid or expired Ring issuer session")

        try:
            env = {
                _EMAIL_ENV: session.email,
                _PASSWORD_ENV: session.password,
                _CODE_ENV: code,
                "PATH": _HELPER_PATH_ENV,
            }
            result = subprocess.run(  # noqa: S603 - fixed helper/node paths from config
                [self.node_path, self.helper_path],
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _auth_error("Ring authentication timed out") from exc
        except OSError as exc:
            raise _auth_error("Unable to start the Ring auth helper") from exc
        finally:
            self.dispose(session_id)

        if result.returncode != 0:
            raise _auth_error("Ring authentication failed")
        token = (result.stdout or "").strip()
        if not self._is_plausible_token(token):
            raise _auth_error("Ring auth helper returned an invalid token")
        return token

    @staticmethod
    def _is_plausible_token(token: str) -> bool:
        """A real wrapped token is a long, single-line, whitespace-free string."""
        return _MIN_TOKEN_LENGTH <= len(token) <= _MAX_TOKEN_LENGTH and not any(
            character.isspace() for character in token
        )

    def dispose(self, session_id: str) -> None:
        """Drop session state (and any held email/password) immediately."""
        self._sessions.pop(session_id, None)
