"""Unit tests for declarative edge hardware sizing profiles."""

from lookout.config.profiles import (
    EDGE_PROFILES,
    apply_edge_profile,
    detect_recommended_profile,
    get_edge_profile,
)
from lookout.config.schema import LookoutConfig


def test_edge_sizing_profiles_registry() -> None:
    assert "raspberry-pi-5-4gb" in EDGE_PROFILES and "intel-n100-mini-pc" in EDGE_PROFILES


def test_apply_edge_profile_updates_config() -> None:
    config = LookoutConfig()
    updated_config = apply_edge_profile(config, "intel-n100-mini-pc")
    assert updated_config.transcode.max_threads == 4 and updated_config.transcode.hwaccel == "vaapi"


def test_detect_recommended_profile() -> None:
    recommended = detect_recommended_profile()
    assert recommended in EDGE_PROFILES
    profile_data = get_edge_profile(recommended)
    assert "name" in profile_data
    assert "transcode" in profile_data
