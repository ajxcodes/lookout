"""Declarative edge hardware sizing profiles and automatic host detection."""

from __future__ import annotations

import logging
import os
import platform
import sys
from typing import Any

from lookout.config.schema import LookoutConfig

logger = logging.getLogger(__name__)

EDGE_PROFILES: dict[str, dict[str, Any]] = {
    "raspberry-pi-5-4gb": {
        "id": "raspberry-pi-5-4gb",
        "name": "Raspberry Pi 5 (4GB RAM)",
        "description": (
            "Lightweight worker pool, tmpfs scratch, V4L2 M2M hardware transcode, "
            "Q4 quantized inference."
        ),
        "target_hardware": "Broadcom BCM2712 (4-core Cortex-A76), 4GB LPDDR4X",
        "transcode": {
            "hwaccel": "v4l2m2m",
            "hwaccel_device": "/dev/video11",
            "max_threads": 2,
            "temp_dir": "/dev/shm",
        },
        "light_ml": {
            "max_entities": 10,
            "confidence_threshold": 0.50,
        },
        "storage": {
            "wal_mode": True,
            "busy_timeout_ms": 10000,
        },
        "recommended_cameras": 3,
    },
    "raspberry-pi-5-8gb": {
        "id": "raspberry-pi-5-8gb",
        "name": "Raspberry Pi 5 (8GB RAM) / AI Kit",
        "description": (
            "Expanded worker pool, 512MB tmpfs scratch, Hailo-8 NPU / V4L2 M2M transcode."
        ),
        "target_hardware": (
            "Broadcom BCM2712 (4-core Cortex-A76), 8GB LPDDR4X, Optional Hailo-8 M.2"
        ),
        "transcode": {
            "hwaccel": "v4l2m2m",
            "hwaccel_device": "/dev/video11",
            "max_threads": 4,
            "temp_dir": "/dev/shm",
        },
        "light_ml": {
            "max_entities": 20,
            "confidence_threshold": 0.45,
        },
        "storage": {
            "wal_mode": True,
            "busy_timeout_ms": 10000,
        },
        "recommended_cameras": 6,
    },
    "intel-n100-mini-pc": {
        "id": "intel-n100-mini-pc",
        "name": "Intel N100 / Alder Lake-N Mini PC",
        "description": (
            "Full Intel QuickSync / VA-API hardware offload, 1GB tmpfs, "
            "hybrid local and cloud cascade."
        ),
        "target_hardware": (
            "Intel N100 / N95 / N200 / N305 4C/4T Intel UHD Graphics, 8-16GB DDR4/DDR5"
        ),
        "transcode": {
            "hwaccel": "vaapi",
            "hwaccel_device": "/dev/dri/renderD128",
            "max_threads": 4,
            "temp_dir": "/dev/shm",
        },
        "light_ml": {
            "max_entities": 25,
            "confidence_threshold": 0.45,
        },
        "storage": {
            "wal_mode": True,
            "busy_timeout_ms": 5000,
        },
        "recommended_cameras": 8,
    },
    "ha-green-yellow": {
        "id": "ha-green-yellow",
        "name": "Home Assistant Green / Yellow (Low-Power SBC)",
        "description": (
            "Conservative low-RAM profile optimized for Home Assistant host constraints "
            "and modest thermals."
        ),
        "target_hardware": "Rockchip RK3566 / CM4 2-4GB RAM",
        "transcode": {
            "hwaccel": "none",
            "hwaccel_device": None,
            "max_threads": 1,
            "temp_dir": "/tmp",
        },
        "light_ml": {
            "max_entities": 8,
            "confidence_threshold": 0.55,
        },
        "storage": {
            "wal_mode": True,
            "busy_timeout_ms": 15000,
        },
        "recommended_cameras": 2,
    },
    "apple-silicon-mac": {
        "id": "apple-silicon-mac",
        "name": "Apple Silicon Mac (M1/M2/M3/M4)",
        "description": (
            "Native VideoToolbox zero-copy acceleration, multi-core ONNX, "
            "high-throughput media pipeline."
        ),
        "target_hardware": "Apple M-series (8-16+ unified cores), 8-64GB Unified Memory",
        "transcode": {
            "hwaccel": "videotoolbox",
            "hwaccel_device": None,
            "max_threads": 4,
            "temp_dir": "/tmp",
        },
        "light_ml": {
            "max_entities": 30,
            "confidence_threshold": 0.45,
        },
        "storage": {
            "wal_mode": True,
            "busy_timeout_ms": 5000,
        },
        "recommended_cameras": 12,
    },
}


def get_edge_profile(profile_name: str) -> dict[str, Any]:
    """Retrieve edge profile dictionary by identifier."""
    if profile_name not in EDGE_PROFILES:
        raise ValueError(
            f"Unknown edge profile '{profile_name}'. Available: {list(EDGE_PROFILES.keys())}"
        )
    return EDGE_PROFILES[profile_name]


def apply_edge_profile(config: LookoutConfig, profile_name: str) -> LookoutConfig:
    """Apply an edge sizing profile's presets to a LookoutConfig instance."""
    profile = get_edge_profile(profile_name)

    cfg_dict = config.model_dump()
    cfg_dict["hardware_profile"] = profile_name

    transcode_overrides = profile.get("transcode", {})
    if "transcode" not in cfg_dict:
        cfg_dict["transcode"] = {}
    for key, val in transcode_overrides.items():
        cfg_dict["transcode"][key] = val

    light_ml_overrides = profile.get("light_ml", {})
    if "light_ml" not in cfg_dict:
        cfg_dict["light_ml"] = {}
    for key, val in light_ml_overrides.items():
        cfg_dict["light_ml"][key] = val

    storage_overrides = profile.get("storage", {})
    if "storage" not in cfg_dict:
        cfg_dict["storage"] = {}
    for key, val in storage_overrides.items():
        cfg_dict["storage"][key] = val

    return LookoutConfig.model_validate(cfg_dict)


def detect_recommended_profile() -> str:
    """Auto-detect host hardware architecture and recommend the best edge profile."""
    # 1. Apple Silicon macOS
    if sys.platform == "darwin":
        return "apple-silicon-mac"

    # 2. Raspberry Pi probing
    try:
        model_file = "/sys/firmware/devicetree/base/model"
        if os.path.isfile(model_file):
            with open(model_file, encoding="utf-8", errors="ignore") as f:
                model_str = f.read().lower()
                if "raspberry pi 5" in model_str:
                    # Check memory size
                    try:
                        mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                        if mem_bytes > 5 * 1024 * 1024 * 1024:
                            return "raspberry-pi-5-8gb"
                    except Exception:
                        pass
                    return "raspberry-pi-5-4gb"
                if "raspberry pi" in model_str:
                    return "raspberry-pi-5-4gb"
    except Exception:
        pass

    # 3. Intel / AMD x86_64 with VA-API
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        if os.path.exists("/dev/dri/renderD128"):
            return "intel-n100-mini-pc"
        return "intel-n100-mini-pc"

    # 4. ARM64 / Low power fallback
    if "arm" in machine or "aarch64" in machine:
        return "ha-green-yellow"

    return "intel-n100-mini-pc"
