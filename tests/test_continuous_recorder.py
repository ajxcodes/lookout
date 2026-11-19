"""Tests for the supervised continuous recorder service."""

import asyncio

import pytest

from lookout.adapters.continuous_recorder import ContinuousRecorderService
from lookout.config.schema import LookoutConfig


class _Cam:
    def __init__(self, cid: str, enabled: bool = True, cr: bool | None = None) -> None:
        self.id = cid
        self.enabled = enabled
        self.continuous_recording = cr


class _Proc:
    def __init__(self, wait_event: asyncio.Event | None = None) -> None:
        self.terminated = False
        self.killed = False
        self._wait_event = wait_event or asyncio.Event()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self._wait_event.set()

    def kill(self) -> None:
        self.killed = True
        self._wait_event.set()


def test_build_ffmpeg_command_segment_layout() -> None:
    recorder = ContinuousRecorderService(
        config=LookoutConfig(),
        cameras=[_Cam("cam1")],
        storage_base_path="/tmp/scratchpad",
    )
    cmd = recorder.build_ffmpeg_command("cam1")
    assert "-f" in cmd and "segment" in cmd and "-segment_time" in cmd
    assert "continuous/cam1/%Y/%m/%d/%H-%M-%S.mp4" in cmd[-1]


def test_opted_out_camera_not_recorded() -> None:
    cam = _Cam("opted-out", cr=False)
    recorder = ContinuousRecorderService(
        config=LookoutConfig(),
        cameras=[cam],
        storage_base_path="/tmp/scratchpad",
    )
    assert "opted-out" not in recorder.status()


@pytest.mark.asyncio
async def test_supervisor_restarts_on_failure() -> None:
    async def factory(*a, **k):
        raise RuntimeError("boom")

    recorder = ContinuousRecorderService(
        config=LookoutConfig(),
        cameras=[_Cam("cam1")],
        storage_base_path="/tmp/scratchpad",
        process_factory=factory,
    )
    await recorder.start()
    await asyncio.sleep(0.3)
    await recorder.stop()
    assert recorder.restart_count >= 1 and recorder.backoff_seconds in (1, 2, 4)


@pytest.mark.asyncio
async def test_graceful_shutdown_terminates() -> None:
    proc = _Proc()

    async def factory(*a, **k):
        return proc

    recorder = ContinuousRecorderService(
        config=LookoutConfig(),
        cameras=[_Cam("cam1")],
        storage_base_path="/tmp/scratchpad",
        process_factory=factory,
    )
    await recorder.start()
    await asyncio.sleep(0.1)
    await recorder.stop()
    assert proc.terminated is True


@pytest.mark.asyncio
async def test_status_reports_running_and_last_segment() -> None:
    proc = _Proc()

    async def factory(*a, **k):
        return proc

    recorder = ContinuousRecorderService(
        config=LookoutConfig(),
        cameras=[_Cam("cam1")],
        storage_base_path="/tmp/scratchpad",
        process_factory=factory,
    )
    await recorder.start()
    await asyncio.sleep(0.1)
    status = recorder.status()
    assert status["cam1"]["running"] is True and status["cam1"]["last_segment"] is not None
    await recorder.stop()
