"""Playwright E2E browser automation test fixtures and background server harness."""

import base64
import socket
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig
from lookout.domain.models import (
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)

TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYW"
    "ICwgIyYnKSopGR8tMC0oMCUoKSj/1wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo"
    "KCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAA"
    "AAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEAA"
    "AAH/9k="
)

TINY_MP4 = (
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42\x00\x00\x00\x08free\x00\x00\x00\x08mdat"
)


class UvicornTestServer(threading.Thread):
    """Background thread running Uvicorn server for E2E tests."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.app = app
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config=config)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=5.0)


def wait_for_server(url: str, timeout: float = 10.0) -> None:
    """Poll server health check until ready or timeout."""
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            with httpx.Client(timeout=1.0) as client:
                res = client.get(f"{url}/health")
                if res.status_code == 200:
                    return
        except Exception:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"Server at {url} failed to respond within {timeout} seconds")


@pytest.fixture
def free_port() -> int:
    """Dynamically allocate and return an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def test_environment(tmp_path: Path) -> dict[str, Any]:
    """Provision isolated temporary directory for test config and storage."""
    config_path = tmp_path / "config.yaml"
    storage_dir = tmp_path / "data"
    storage_dir.mkdir(parents=True, exist_ok=True)
    media_dir = storage_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    faces_dir = storage_dir / "faces"
    faces_dir.mkdir(parents=True, exist_ok=True)

    # Seed mock camera directories and media
    for cam in ("front-porch", "backyard"):
        cam_dir = media_dir / cam
        cam_dir.mkdir(parents=True, exist_ok=True)
        (cam_dir / "evt-001.jpg").write_bytes(TINY_JPEG)
        (cam_dir / "evt-001.mp4").write_bytes(TINY_MP4)
        (cam_dir / "evt-002.jpg").write_bytes(TINY_JPEG)
        (cam_dir / "evt-002.mp4").write_bytes(TINY_MP4)

    # Seed mock face reference
    resident_dir = faces_dir / "default"
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "alice.jpg").write_bytes(TINY_JPEG)

    return {
        "root": tmp_path,
        "config_path": config_path,
        "storage_dir": storage_dir,
        "media_dir": media_dir,
        "faces_dir": faces_dir,
    }


@pytest.fixture
def configured_server(
    free_port: int, test_environment: dict[str, Any]
) -> Generator[dict[str, Any], None, None]:
    """Provision and run a configured Lookout server with seeded cameras and events."""
    config = LookoutConfig(
        system_name="Lookout Test",
        storage=StorageConfig(
            base_path=str(test_environment["storage_dir"]),
            database_path=str(test_environment["storage_dir"] / "lookout.db"),
        ),
        cameras=[
            CameraConfig(
                id="front-porch",
                name="Front Porch",
                stream_url="rtsp://localhost:8554/front_porch",
                camera_type="generic",
                enabled=True,
            ),
            CameraConfig(
                id="backyard",
                name="Backyard",
                stream_url="rtsp://localhost:8554/backyard",
                camera_type="generic",
                enabled=True,
            ),
        ],
    )

    repository = InMemoryEventRepository()
    now = datetime.now(UTC)
    event1 = CameraEvent(
        id="evt-001",
        camera_id="front-porch",
        timestamp=now,
        category=EventCategory.COURIER_DELIVERY,
        threat_level=ThreatLevel.LOW,
        summary="Courier delivered package at front porch",
        metrics=DetectionMetrics(confidence=0.95, audio_decibels=-15.0),
        provenance=[
            InferenceProvenance(
                provider="ollama",
                model_name="moondream",
                latency_ms=120.0,
                confidence=0.95,
            )
        ],
    )
    event2 = CameraEvent(
        id="evt-002",
        camera_id="front-porch",
        timestamp=now,
        category=EventCategory.VERIFIED_RESIDENT,
        threat_level=ThreatLevel.INFO,
        summary="Resident detected near entryway",
        metrics=DetectionMetrics(confidence=0.98, audio_decibels=-22.0),
        provenance=[
            InferenceProvenance(
                provider="opencode",
                model_name="deepseek-v4.1-flash",
                latency_ms=250.0,
                confidence=0.98,
            )
        ],
    )
    repository._events[event1.id] = event1
    repository._events[event2.id] = event2

    app = create_app(
        config=config,
        repository=repository,
        config_path=test_environment["config_path"],
    )

    host = "127.0.0.1"
    server = UvicornTestServer(app=app, host=host, port=free_port)
    server.start()

    base_url = f"http://{host}:{free_port}"
    ingress_base_url = f"http://{host}:{free_port}/api/hassio_ingress/test-token/"
    wait_for_server(base_url)

    yield {
        "port": free_port,
        "host": host,
        "standalone_base_url": base_url,
        "ingress_base_url": ingress_base_url,
        "app": app,
        "repository": repository,
        "config": config,
        "env": test_environment,
    }

    server.stop()


@pytest.fixture
def unconfigured_server(free_port: int, tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Provision and run an unconfigured Lookout server triggering onboarding setup wizard."""
    config_path = tmp_path / "lookout_unconfigured.yaml"
    repository = InMemoryEventRepository()
    app = create_app(
        config=None,
        repository=repository,
        config_path=config_path,
    )

    host = "127.0.0.1"
    server = UvicornTestServer(app=app, host=host, port=free_port)
    server.start()

    base_url = f"http://{host}:{free_port}"
    ingress_base_url = f"http://{host}:{free_port}/api/hassio_ingress/test-token/"
    wait_for_server(base_url)

    yield {
        "port": free_port,
        "host": host,
        "standalone_base_url": base_url,
        "ingress_base_url": ingress_base_url,
        "config_path": config_path,
        "app": app,
        "repository": repository,
    }

    server.stop()


@pytest.fixture
def lookout_server(configured_server: dict[str, Any]) -> dict[str, Any]:
    """Fixture alias for configured Lookout server."""
    return configured_server


@pytest.fixture
def standalone_base_url(configured_server: dict[str, Any]) -> str:
    """Fixture returning base URL for standalone mode."""
    return str(configured_server["standalone_base_url"])


@pytest.fixture
def ingress_base_url(configured_server: dict[str, Any]) -> str:
    """Fixture returning base URL for simulated Ingress mode."""
    return str(configured_server["ingress_base_url"])
