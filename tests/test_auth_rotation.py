"""AC4/AC6 - forced rotation, Ring pnc preservation and rollback."""

from __future__ import annotations

from pathlib import Path

import yaml
from auth_test_helpers import (
    FakeRingIssuer,
    ReachableNativeAdapter,
    inject_fake_issuers,
    make_client,
)

from lookout.adapters.ring_native import decode_ring_token, encode_ring_token
from lookout.config.schema import LookoutConfig, RingConfig

_ORIGINAL_PNC = {
    "token": "push-credential-token",
    "machines": [{"id": "machine-1", "token": "push-machine-token"}],
}
_ORIGINAL = encode_ring_token({"rt": "old-refresh", "hid": "old-hardware-id", "pnc": _ORIGINAL_PNC})
_NEW_ISSUED = encode_ring_token({"rt": "new-refresh", "hid": "new-hardware-id"})


def _configured_client(tmp_path: Path, reachable: bool = True):
    config = LookoutConfig(
        ring=RingConfig(
            email="ring@example.com",
            refresh_token=_ORIGINAL,
            auth_token=_ORIGINAL,
        )
    )
    client, app, config_path = make_client(
        tmp_path, config, reachable=reachable, with_config_file=True
    )
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
    return client, app, config_path, config


def _rotate(client, code: str = "654321"):
    return client.post(
        "/api/auth/ring/rotate",
        json={
            "email": "ring@example.com",
            "password": "real-password",
            "code": code,
        },
    )


def test_rotate_forces_reauth_when_preauthenticated(tmp_path: Path) -> None:
    """TR-9 (AC4): rotate invokes the issuer despite a valid existing token."""
    issuer = FakeRingIssuer(token=_NEW_ISSUED)
    inject_fake_issuers(ring=issuer)
    client, _app, _path, _config = _configured_client(tmp_path)

    response = _rotate(client)
    assert response.status_code == 200
    assert response.json()["status"] == "authenticated"
    assert issuer.begin_calls == 1
    assert issuer.complete_calls == 1


def test_ring_rotation_preserves_pnc_and_merges_rt_hid(tmp_path: Path) -> None:
    """TR-10 (AC6): pnc survives, rt/hid are replaced, backup is created."""
    inject_fake_issuers(ring=FakeRingIssuer(token=_NEW_ISSUED))
    client, _app, config_path, config = _configured_client(tmp_path)

    response = _rotate(client)
    assert response.status_code == 200
    assert response.json()["status"] == "authenticated"

    written = decode_ring_token(config.ring.refresh_token or "")
    assert written["pnc"] == _ORIGINAL_PNC
    assert written["rt"] == "new-refresh"
    assert written["hid"] == "new-hardware-id"
    assert list(config_path.parent.glob("config.yaml.bak.*"))


def test_failed_verification_rolls_back_config_from_backup(tmp_path: Path) -> None:
    """TR-11 (AC5/AC6): unreachable verification restores the prior token."""
    inject_fake_issuers(ring=FakeRingIssuer(token=_NEW_ISSUED))
    client, app, _path, config = _configured_client(tmp_path, reachable=False)
    app.state.ring_native = ReachableNativeAdapter(reachable=False, provider="ring")

    response = _rotate(client)
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert config.ring.refresh_token == _ORIGINAL
