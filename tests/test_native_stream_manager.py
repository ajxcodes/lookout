"""Tests for the embedded go2rtc stream manager using a fake process and HTTP transport."""

from __future__ import annotations

import asyncio
import os
import stat
from typing import Any

import httpx
import pytest
import yaml

from lookout.adapters.native_stream_manager import (
    Go2RtcUnavailableError,
    MediaCaptureError,
    NativeAwareRecorder,
    NativeStreamManager,
)
from lookout.config.schema import CameraConfig, Go2RtcConfig, LookoutConfig

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-frame" * 128
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08mdat" + b"\x00" * 256


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = 0
        return 0


class FakeGo2Rtc:
    """Minimal in-memory go2rtc HTTP API."""

    def __init__(self) -> None:
        self.streams: dict[str, str] = {}
        self.puts: list[tuple[str, str]] = []
        self.frame_bytes = JPEG
        self.clip_bytes = MP4
        self.frame_status = 200
        self.clip_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/streams" and request.method == "PUT":
            name = request.url.params["name"]
            src = request.url.params["src"]
            self.puts.append((name, src))
            self.streams[name] = src
            return httpx.Response(200, json={})
        if path == "/api/streams" and request.method == "DELETE":
            self.streams.pop(request.url.params["src"], None)
            return httpx.Response(200, json={})
        if path == "/api/streams":
            return httpx.Response(200, json={name: [src] for name, src in self.streams.items()})
        if path == "/api/frame.jpeg":
            return httpx.Response(self.frame_status, content=self.frame_bytes)
        if path == "/api/stream.mp4":
            return httpx.Response(self.clip_status, content=self.clip_bytes)
        return httpx.Response(404)


def _manager(tmp_path: Any, api: FakeGo2Rtc, **kwargs: Any) -> tuple[NativeStreamManager, Any]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    binary = tmp_path / "go2rtc"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    manager = NativeStreamManager(
        config=LookoutConfig(
            go2rtc=Go2RtcConfig(native_api_port=11984, native_runtime_dir=str(tmp_path))
        ),
        binary_path=str(binary),
        runtime_dir=tmp_path,
        client=client,
        process_factory=lambda *_args: _return_process(),
        **kwargs,
    )
    return manager, client


async def _return_process() -> FakeProcess:
    return FakeProcess()


def test_config_generation_contains_loopback_listeners(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    manager.streams["cam1"] = "rtsp://example/cam1"
    manager._write_config()
    text = (tmp_path / "go2rtc.yaml").read_text(encoding="utf-8")

    assert 'api:\n  listen: "127.0.0.1:' in text
    assert 'rtsp:\n  listen: ""' in text
    assert 'webrtc:\n  listen: ""' in text
    assert 'streams:\n  "cam1": "rtsp://example/cam1"' in text


def test_empty_streams_config_is_patchable_yaml(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, _client = _manager(tmp_path, api)
    manager._write_config()
    text = (tmp_path / "go2rtc.yaml").read_text(encoding="utf-8")

    # A bare `streams:` key is what go2rtc expects when it patches its own file.
    assert "streams:\n" in text
    assert "{}" not in text
    parsed = yaml.safe_load(text)
    assert parsed["streams"] is None


async def test_default_process_factory_captures_stdout_and_stderr(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    log_path = tmp_path / "go2rtc.log"
    try:
        await manager._default_process_factory(str(tmp_path / "go2rtc"), tmp_path / "go2rtc.yaml")

        kwargs = captured["kwargs"]
        assert kwargs["stdout"] is kwargs["stderr"]
        assert kwargs["stdout"] is manager._log_handle
        handle_stat = os.fstat(manager._log_handle.fileno())
        log_stat = log_path.stat()
        assert (handle_stat.st_dev, handle_stat.st_ino) == (log_stat.st_dev, log_stat.st_ino)
        assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
        if os.name == "posix":
            assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    finally:
        if manager._log_handle is not None:
            manager._log_handle.close()
            manager._log_handle = None
        await client.aclose()


async def test_runtime_files_are_owner_only(tmp_path: Any) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file permissions only")

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    os.chmod(runtime, 0o755)
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    manager.runtime_dir = runtime
    try:
        manager._write_config()

        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
        assert stat.S_IMODE((runtime / "go2rtc.yaml").stat().st_mode) == 0o600
    finally:
        await client.aclose()


async def test_start_stop_and_ensure_stream(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    try:
        assert manager.running is False
        await manager.start()
        assert manager.running is True
        assert manager.api_url is not None

        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert manager.streams["cam1"] == "rtsp://example/cam1"
        assert api.puts == [("cam1", "rtsp://example/cam1")]

        # Idempotent: the same source is not re-registered.
        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert len(api.puts) == 1

        # A changed source is re-registered.
        await manager.ensure_stream("cam1", "rtsp://example/cam2")
        assert len(api.puts) == 2
        assert manager.streams["cam1"] == "rtsp://example/cam2"

        status = await manager.status()
        assert status["running"] is True
        assert status["streams"] == {"cam1": "rtsp://example/cam2"}
        assert status["binary"]

        await manager.stop()
        assert manager.running is False
        await manager.stop()
        assert manager.running is False
    finally:
        await client.aclose()


async def test_frame_and_clip_return_validated_media(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    try:
        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert await manager.frame("cam1") == JPEG
        assert await manager.clip("cam1", 5) == MP4
    finally:
        await client.aclose()


async def test_frame_rejects_empty_and_non_jpeg(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    try:
        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        api.frame_bytes = b""
        with pytest.raises(MediaCaptureError):
            await manager.frame("cam1")
        api.frame_bytes = b"not-a-jpeg"
        with pytest.raises(MediaCaptureError):
            await manager.frame("cam1")
        api.frame_status = 500
        with pytest.raises(MediaCaptureError):
            await manager.frame("cam1")
    finally:
        await client.aclose()


async def test_clip_rejects_empty_and_non_mp4(tmp_path: Any) -> None:
    api = FakeGo2Rtc()
    manager, client = _manager(tmp_path, api)
    try:
        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        api.clip_bytes = b""
        with pytest.raises(MediaCaptureError):
            await manager.clip("cam1", 5)
        api.clip_bytes = b"not-an-mp4"
        with pytest.raises(MediaCaptureError):
            await manager.clip("cam1", 5)
        api.clip_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
        with pytest.raises(MediaCaptureError, match="no media samples"):
            await manager.clip("cam1", 5)
        api.clip_status = 404
        with pytest.raises(MediaCaptureError):
            await manager.clip("cam1", 5)
    finally:
        await client.aclose()


async def test_missing_binary_raises_actionable_error(tmp_path: Any) -> None:
    manager = NativeStreamManager(
        config=LookoutConfig(go2rtc=Go2RtcConfig()),
        binary_path=str(tmp_path / "does-not-exist"),
        runtime_dir=tmp_path,
    )

    with pytest.raises(Go2RtcUnavailableError) as excinfo:
        await manager.start()
    assert "go2rtc binary not found" in str(excinfo.value)


async def test_native_aware_recorder_routes_by_provider(tmp_path: Any) -> None:
    class Recorder:
        def __init__(self, tag: str) -> None:
            self.tag = tag
            self.calls: list[str] = []

        async def capture_snapshot(self, camera_id: str) -> str:
            self.calls.append(camera_id)
            return f"{self.tag}:{camera_id}.jpg"

        async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
            return f"{self.tag}:{camera_id}.mp4"

    config = LookoutConfig(
        cameras=[
            CameraConfig(
                id="door",
                name="Door",
                stream_url="ring:?camera_id=1&device_id=abc",
                native_provider="ring",
            ),
            CameraConfig(
                id="office",
                name="Office",
                stream_url="wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x",
                native_provider="wyze",
            ),
            CameraConfig(id="rtsp", name="RTSP", stream_url="rtsp://example/cam"),
        ]
    )
    wyze = Recorder("wyze")
    ring = Recorder("ring")
    fallback = Recorder("fallback")
    recorder = NativeAwareRecorder(config=config, wyze=wyze, ring=ring, fallback=fallback)

    assert await recorder.capture_snapshot("door") == "ring:door.jpg"
    assert await recorder.capture_snapshot("office") == "wyze:office.jpg"
    assert await recorder.capture_snapshot("rtsp") == "fallback:rtsp.jpg"
    assert wyze.calls == ["office"]
    assert ring.calls == ["door"]
