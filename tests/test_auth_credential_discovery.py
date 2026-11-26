"""AC3 - skip-when-configured discovery across config, env and sidecar sources."""

from __future__ import annotations

import os
from pathlib import Path

from auth_test_helpers import FakeRingIssuer

from lookout.adapters.ring_native import encode_ring_token
from lookout.config.schema import LookoutConfig, RingConfig, WyzeConfig
from lookout.providers import auth_service, credential_probe
from lookout.providers.ring.driver import RingProviderDriver

_VALID_ENVELOPE = encode_ring_token(
    {"rt": "real-ring-refresh", "hid": "hardware-id", "pnc": {"token": "push"}}
)


def _write_sidecar(tmp_path: Path, envelope: str = _VALID_ENVELOPE) -> Path:
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    env_file = sidecar_dir / "ring-mqtt.env"
    env_file.write_text(f"RINGTOKEN={envelope}\nMQTTHOST=localhost\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    return sidecar_dir


def test_sidecar_env_token_triggers_pre_authenticated_skip(tmp_path: Path) -> None:
    """TR-7 (AC3): sidecar token yields pre_authenticated and skips the issuer."""
    sidecar_dir = _write_sidecar(tmp_path)
    issuer = FakeRingIssuer()
    driver = RingProviderDriver(token_issuer=issuer)

    result = auth_service.login(
        "ring",
        {"email": "ring@example.com", "password": "pw"},
        LookoutConfig(),
        driver=driver,
        sidecar_dir=sidecar_dir,
        environ={},
    )
    assert result["status"] == "pre_authenticated"
    assert result["source"] == "sidecar"
    assert issuer.begin_calls == 0


def test_env_and_config_tokens_are_detected(tmp_path: Path) -> None:
    """TR-8 (AC3): config/env tokens are detected with the right source."""
    config = LookoutConfig(ring=RingConfig(refresh_token=_VALID_ENVELOPE))
    config_probe = credential_probe.ring_credentials_present(
        config, environ={}, sidecar_dir=tmp_path / "none"
    )
    assert config_probe["present"] is True
    assert config_probe["source"] == "config"

    env_probe = credential_probe.ring_credentials_present(
        LookoutConfig(),
        environ={"RINGTOKEN": _VALID_ENVELOPE},
        sidecar_dir=tmp_path / "none",
    )
    assert env_probe["present"] is True
    assert env_probe["source"] == "env"

    wyze_probe = credential_probe.wyze_credentials_present(
        LookoutConfig(),
        environ={"WYZE_ACCESS_TOKEN": "some-access-token"},
        sidecar_dir=tmp_path / "none",
    )
    assert wyze_probe["present"] is True
    assert wyze_probe["source"] == "env"

    # No token value is leaked in any returned result.
    for probe in (config_probe, env_probe, wyze_probe):
        assert _VALID_ENVELOPE not in str(probe)
        assert "some-access-token" not in str(probe)

    # The DiscoveryResult is likewise redacted.
    driver = RingProviderDriver()
    discovery = driver.probe_existing(config)
    assert _VALID_ENVELOPE not in str(discovery.model_dump())


def test_wyze_config_and_sidecar_detection(tmp_path: Path) -> None:
    """Wyze config/sidecar tokens are discovered without exposing values."""
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "wyze-bridge.env").write_text(
        "WYZE_ACCESS_TOKEN=sidecar-access\n", encoding="utf-8"
    )
    probe = credential_probe.wyze_credentials_present(
        LookoutConfig(wyze=WyzeConfig()), environ={}, sidecar_dir=sidecar_dir
    )
    assert probe["present"] is True and probe["source"] == "sidecar"
    assert "sidecar-access" not in str(probe)
