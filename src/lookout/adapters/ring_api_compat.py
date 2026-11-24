"""Ring client compatibility helpers for the ``clients_api/session`` authz gate.

``ring-doorbell`` hard-codes a legacy ``POST /clients_api/session`` bootstrap
before every query. Ring's ext_authz gateway rejects that legacy endpoint with
HTTP 406 ``ext_authz_denied`` for accounts whose OAuth token otherwise works,
while the actual data endpoints (``ring_devices``, ``doorbots/<id>/history``,
``dings/active``) accept the OAuth bearer token directly. The helpers here keep
a credential-free device profile set (for rotation on other 406s) and provide
:func:`skip_legacy_session`, which marks a Ring client as session-initialised so
the rejected bootstrap is never issued. No token, password or hardware id is
ever stored here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RingClientProfile:
    """A device-compatible Ring client signature."""

    name: str
    user_agent: str
    api_version: int = 11


DEFAULT_PROFILES: tuple[RingClientProfile, ...] = (
    RingClientProfile("android-6.62.0", "android:com.ringapp:6.62.0", 11),
    RingClientProfile("android-generic", "android:com.ringapp", 11),
    RingClientProfile("ios-6.62.0", "ios:com.ringapp:6.62.0", 11),
    RingClientProfile("ios-generic", "ios:com.ringapp", 11),
)

_PROFILE_FILE = "ring_client_profile.json"

# Only a standalone "406" indicates the ext_authz rejection. A bare substring
# check would misfire on ports (10406), latencies (406ms) and event ids.
_HTTP_406_RE = re.compile(r"\b406\b")


def is_authz_denied(exc: Any) -> bool:
    """True when an exception represents Ring's ext_authz/406 session rejection."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    text = str(exc).lower()
    markers = ("ext_authz_denied", "clients_api/session", "not acceptable", "ring_authz_denied")
    return (
        status_code == 406
        or _HTTP_406_RE.search(text) is not None
        or any(marker in text for marker in markers)
    )


def apply(profile: RingClientProfile) -> None:
    """Patch the API version ring-doorbell sends on every clients_api request."""
    # Imported lazily so unit tests can patch sys.modules without the dependency.
    try:
        import ring_doorbell.ring as ring_module
    except ImportError:  # pragma: no cover - dependency guard
        ring_module = None  # type: ignore[assignment]
    api_version = str(profile.api_version)
    if ring_module is not None:
        ring_module.API_VERSION = api_version  # type: ignore[attr-defined]
    try:
        import ring_doorbell.const as const_module
    except ImportError:  # pragma: no cover - dependency guard
        const_module = None  # type: ignore[assignment]
    if const_module is not None:
        const_module.API_VERSION = api_version


# ring-doorbell only POSTs /clients_api/session when ``Ring.session is None``.
# That bootstrap is rejected by Ring's ext_authz gateway with 406 even when the
# OAuth token and every data endpoint work. A credential-free sentinel lets the
# normal query path run without the legacy session request.
_SESSIONLESS_SENTINEL = {"clients_api_session": "skipped"}


def skip_legacy_session(ring: Any) -> None:
    """Mark a Ring client session-ready without the 406-prone session bootstrap.

    ``ring_doorbell.Ring.async_update_devices`` and ``async_history`` call
    ``async_create_session()`` only while ``ring.session is None``. Seeding the
    sentinel preserves the library's normal bearer-token query path while
    avoiding ``POST /clients_api/session``, which Ring's ext_authz gateway
    rejects. An existing session (including a real one) is left untouched.
    """
    if getattr(ring, "session", None) is None:
        ring.session = dict(_SESSIONLESS_SENTINEL)


class RingProfileState:
    """Rotate and persist the active client profile index (no credentials)."""

    def __init__(
        self,
        profiles: tuple[RingClientProfile, ...] | list[RingClientProfile],
        index: int = 0,
        path: str | Path | None = None,
    ) -> None:
        self.profiles: tuple[RingClientProfile, ...] = tuple(profiles) or DEFAULT_PROFILES
        self._index = index % len(self.profiles) if self.profiles else 0
        self.path = Path(path) if path is not None else None

    @classmethod
    def from_config(
        cls, config: Any | None, runtime_dir: str | Path | None = None
    ) -> RingProfileState:
        profiles = list(DEFAULT_PROFILES)
        override_agent = getattr(config, "api_user_agent", None)
        override_version = getattr(config, "api_version", None)
        if override_agent or override_version:
            base = profiles[0]
            profiles.insert(
                0,
                RingClientProfile(
                    name="config-override",
                    user_agent=str(override_agent) if override_agent else base.user_agent,
                    api_version=int(override_version) if override_version else base.api_version,
                ),
            )
        path = Path(runtime_dir) / _PROFILE_FILE if runtime_dir is not None else None
        index = 0
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                candidate = int(data.get("profile_index", 0))
                if 0 <= candidate < len(profiles):
                    index = candidate
            except (OSError, ValueError, TypeError):
                logger.debug("Ignoring unreadable Ring client profile state at %s", path)
        return cls(tuple(profiles), index=index, path=path)

    def current(self) -> RingClientProfile:
        return self.profiles[self._index]

    def rotate(self) -> RingClientProfile:
        if len(self.profiles) > 1:
            self._index = (self._index + 1) % len(self.profiles)
        return self.current()

    def mark_success(self) -> None:
        """Persist the currently active profile as the last known-good one."""
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.write_text(json.dumps({"profile_index": self._index}), encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)
        except OSError:
            logger.debug("Could not persist Ring client profile index to %s", self.path)
