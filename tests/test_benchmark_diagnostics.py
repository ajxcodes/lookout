"""Unit and API integration tests for benchmark diagnostics engine and REST endpoints."""

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.benchmark import BenchmarkEngine
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig


@pytest.mark.asyncio
async def test_benchmark_diagnostics_report() -> None:
    engine = BenchmarkEngine(config=LookoutConfig())
    report = await engine.run_quick_benchmark()
    assert report.recommended_max_cameras >= 1 and report.sqlite_iops > 0
    assert report.transcode_fps > 0.0
    assert report.inference_latency_ms > 0.0


def test_system_benchmark_rest_api() -> None:
    config = LookoutConfig(system_name="Benchmark Test")
    app = create_app(config=config, repository=InMemoryEventRepository())
    client = TestClient(app)

    response = client.get("/api/system/benchmark")
    assert response.status_code == 200 and "transcode_fps" in response.json()
    data = response.json()
    assert "hardware" in data
    assert "recommended_max_cameras" in data
    assert "sqlite_iops" in data
