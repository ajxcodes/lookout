"""Unit tests for audio-preserving recording configuration and media capture."""

from __future__ import annotations

import shutil
from typing import Any

import httpx
import pytest
import respx

import lookout.adapters.native_stream_manager as nsm
from lookout.adapters.continuous_recorder import ContinuousRecorderService
from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.native_stream_manager import MediaCaptureError, NativeStreamManager
from lookout.adapters.tempfile import TempMediaManager
from lookout.adapters.web.app import create_app
from lookout.config.schema import (
    CameraConfig,
    Go2RtcConfig,
    LookoutConfig,
    TranscodeConfig,
    resolve_record_audio,
)

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-frame" * 8
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08mdat" + b"\x00" * 64


def _pairs(cmd: list[str]) -> set[tuple[str, str]]:
    return set(zip(cmd, cmd[1:], strict=False))


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0

    async def wait(self) -> int:
        self.returncode = 0
        return 0


async def _return_process(*args: Any, **kwargs: Any) -> _FakeProcess:
    return _FakeProcess()


class _Go2RtcStub:
    """Minimal go2rtc HTTP API that records every incoming request."""

    def __init__(self, *, codecs: list[str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.streams: dict[str, str] = {}
        self.codecs = codecs
        self.clip_bytes = MP4

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "PUT" and path == "/api/streams":
            self.streams[request.url.params["name"]] = request.url.params["src"]
            return httpx.Response(200, json={})
        if request.method == "DELETE" and path == "/api/streams":
            self.streams.pop(request.url.params.get("src", ""), None)
            return httpx.Response(200, json={})
        if path == "/api/streams":
            if "src" in request.url.params:
                medias = [f"video, recvonly, {c}" for c in (self.codecs or [])]
                return httpx.Response(200, json={"producers": [{"medias": medias}]})
            return httpx.Response(200, json={name: [src] for name, src in self.streams.items()})
        if path == "/api/frame.jpeg":
            return httpx.Response(200, content=JPEG)
        if path == "/api/stream.mp4":
            return httpx.Response(200, content=self.clip_bytes)
        return httpx.Response(404)


def _manager(
    tmp_path: Any, config: LookoutConfig, stub: _Go2RtcStub
) -> tuple[NativeStreamManager, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handler))
    binary = tmp_path / "go2rtc"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    manager = NativeStreamManager(
        config=config,
        binary_path=str(binary),
        runtime_dir=tmp_path,
        client=client,
        process_factory=_return_process,
    )
    return manager, client


def _clips(stub: _Go2RtcStub) -> list[httpx.Request]:
    return [r for r in stub.requests if r.url.path == "/api/stream.mp4"]


def _mock_ffmpeg_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the host-dependent ffmpeg probe in the go2rtc manager.

    ``ensure_h264_stream`` calls ``shutil.which("ffmpeg")`` and raises when the
    binary is absent. CI runners have no ffmpeg, so patch the probe to report an
    available binary while delegating every other lookup to the real ``which``.
    This touches only the test process and is reverted by ``monkeypatch``.
    """
    real_which = shutil.which

    def fake_which(cmd: str, *args: Any, **kwargs: Any) -> str | None:
        if cmd == "ffmpeg":
            return "/usr/bin/ffmpeg"
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(nsm.shutil, "which", fake_which)


def test_resolve_record_audio_precedence() -> None:
    """TR-1: default, global, per-camera override and camera-id string lookups."""
    cam = type("Cam", (), {"id": "c1", "record_audio": None})()
    assert resolve_record_audio(cam, LookoutConfig()) is True
    global_off = LookoutConfig(transcode=TranscodeConfig(record_audio=False))
    assert resolve_record_audio(cam, global_off) is False

    override_off = type("Cam2", (), {"id": "c2", "record_audio": False})()
    assert resolve_record_audio(override_off, LookoutConfig()) is False

    override_on = type("Cam3", (), {"id": "c3", "record_audio": True})()
    assert resolve_record_audio(override_on, global_off) is True

    configured = LookoutConfig(
        cameras=[CameraConfig(id="c2", name="C2", stream_url="rtsp://x/c2", record_audio=False)]
    )
    assert resolve_record_audio("c2", configured) is False
    assert resolve_record_audio("unknown", configured) is True


def test_build_ffmpeg_command_includes_audio_when_enabled() -> None:
    """TR-2: audio-on command maps and AAC-encodes the microphone track."""
    recorder = ContinuousRecorderService(
        config=LookoutConfig(), cameras=[], storage_base_path="/tmp/audio-smoke"
    )
    cmd = recorder.build_ffmpeg_command("cam1")
    joined = " ".join(cmd)
    assert "video=h264,h265&audio=all" in joined
    pairs = _pairs(cmd)
    assert ("-map", "0:v:0") in pairs
    assert ("-map", "0:a:0?") in pairs
    assert ("-c:v", "copy") in pairs
    assert ("-c:a", "aac") in pairs
    assert ("-b:a", "96k") in pairs
    assert ("-f", "segment") in pairs
    assert ("-reset_timestamps", "1") in pairs
    assert ("-strftime", "1") in pairs
    assert ("-strftime_mkdir", "1") in pairs
    assert "continuous/cam1/%Y/%m/%d/%H-%M-%S.mp4" in cmd[-1]


def test_build_ffmpeg_command_omits_audio_when_disabled() -> None:
    """TR-3: audio-off command requests video only and disables audio."""
    recorder = ContinuousRecorderService(
        config=LookoutConfig(transcode=TranscodeConfig(record_audio=False)),
        cameras=[],
        storage_base_path="/tmp/audio-smoke",
    )
    cmd = recorder.build_ffmpeg_command("cam1")
    joined = " ".join(cmd)
    assert "video=h264,h265" in joined
    assert "video=h264,h265&audio=all" not in joined
    assert "-an" in cmd
    assert "0:v:0" in cmd
    assert "0:a:0?" not in cmd
    assert "-c:a" not in cmd
    assert "96k" not in cmd
    assert ("-c:v", "copy") in _pairs(cmd)


async def test_native_stream_manager_clip_requests_all_audio_codecs(tmp_path: Any) -> None:
    """TR-4: manager clip requests mp4=all when audio is on, none when off."""
    on_stub = _Go2RtcStub()
    on_manager, on_client = _manager(tmp_path, LookoutConfig(), on_stub)
    try:
        await on_manager.start()
        await on_manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert await on_manager.clip("cam1", 10.0) == MP4
        clips = _clips(on_stub)
        assert len(clips) == 1
        assert clips[0].url.params["src"] == "cam1"
        assert clips[0].url.params["duration"] == "10"
        assert clips[0].url.params["mp4"] == "all"
    finally:
        await on_client.aclose()

    off_dir = tmp_path / "off"
    off_dir.mkdir()
    off_stub = _Go2RtcStub()
    off_config = LookoutConfig(transcode=TranscodeConfig(record_audio=False))
    off_manager, off_client = _manager(off_dir, off_config, off_stub)
    try:
        await off_manager.start()
        await off_manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert await off_manager.clip("cam1", 10.0) == MP4
        assert "mp4" not in _clips(off_stub)[0].url.params
    finally:
        await off_client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_go2rtc_record_clip_requests_all_audio_codecs(tmp_path: Any) -> None:
    """TR-5: go2rtc fallback clip requests mp4=all when audio is on."""
    route = respx.get("http://localhost:1984/api/stream.mp4").respond(200, content=MP4)
    adapter = Go2RtcAdapter(
        config=Go2RtcConfig(api_url="http://localhost:1984"),
        temp_manager=TempMediaManager(preferred_temp_dir=str(tmp_path)),
        lookout_config=LookoutConfig(),
    )
    result = await adapter.record_clip("cam_front", 10.0)
    assert result.endswith(".mp4")
    assert route.called
    params = route.calls[0].request.url.params
    assert params["src"] == "cam_front"
    assert params["duration"] == "10.0"
    assert params["mp4"] == "all"

    respx.reset()
    off_route = respx.get("http://localhost:1984/api/stream.mp4").respond(200, content=MP4)
    off_adapter = Go2RtcAdapter(
        config=Go2RtcConfig(api_url="http://localhost:1984"),
        temp_manager=TempMediaManager(preferred_temp_dir=str(tmp_path)),
        lookout_config=LookoutConfig(transcode=TranscodeConfig(record_audio=False)),
    )
    off_result = await off_adapter.record_clip("cam_front", 10.0)
    assert off_result.endswith(".mp4")
    assert "mp4" not in off_route.calls[0].request.url.params


async def test_ensure_h264_stream_includes_audio_transcode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TR-6: compatibility H.264 producer keeps an AAC audio track."""
    _mock_ffmpeg_available(monkeypatch)
    stub = _Go2RtcStub()
    manager, client = _manager(tmp_path, LookoutConfig(), stub)
    try:
        await manager.start()
        await manager.ensure_h264_stream("cam1")
        puts = [r for r in stub.requests if r.method == "PUT" and r.url.path == "/api/streams"]
        assert any(r.url.params["name"] == "cam1_h264" for r in puts)
        assert any("ffmpeg:cam1#video=h264#audio=aac" in r.url.params["src"] for r in puts)
    finally:
        await client.aclose()


async def test_ensure_h264_transcode_includes_audio_transcode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TR-7: cold-path H.264 transcode producer keeps an AAC audio track."""
    _mock_ffmpeg_available(monkeypatch)
    stub = _Go2RtcStub(codecs=["H265"])
    manager, client = _manager(tmp_path, LookoutConfig(), stub)
    try:
        await manager.start()
        await manager.ensure_stream("ring_cam", "ring://cam?refresh_token=secret")
        assert await manager.ensure_h264_transcode("ring_cam") is True
        puts = [r for r in stub.requests if r.method == "PUT" and r.url.path == "/api/streams"]
        assert any(r.url.params["name"] == "ring_cam_raw" for r in puts)
        expected = (
            "ffmpeg:ring_cam_raw#video=h264#audio=aac"
            "#raw=-probesize 5000000 -analyzeduration 5000000"
        )
        encoded = (
            "ffmpeg%3Aring_cam_raw%23video%3Dh264%23audio%3Daac"
            "%23raw%3D-probesize+5000000+-analyzeduration+5000000"
        )
        assert any(
            r.url.params["name"] == "ring_cam"
            and (r.url.params["src"] == expected or encoded in str(r.url))
            for r in puts
        )
    finally:
        await client.aclose()


def test_create_app_wires_record_audio_into_media_adapters() -> None:
    """TR-8: create_app passes resolved config into both clip adapters."""
    app = create_app(
        config=LookoutConfig(transcode=TranscodeConfig(record_audio=False)),
        repository=InMemoryEventRepository(),
    )
    assert app.state.native_stream_manager.record_audio is False
    assert app.state.ptz_service.video_recorder.fallback.record_audio is False

    default_app = create_app(config=LookoutConfig(), repository=InMemoryEventRepository())
    assert default_app.state.native_stream_manager.record_audio is True
    assert default_app.state.ptz_service.video_recorder.fallback.record_audio is True


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_fallback_survives_audio_change(tmp_path: Any) -> None:
    """TR-9: a failed clip still falls back to a JPEG snapshot with config wired."""
    respx.get("http://localhost:1984/api/stream.mp4").respond(503)
    respx.get("http://localhost:1984/api/frame.jpeg").respond(200, content=JPEG)
    adapter = Go2RtcAdapter(
        config=Go2RtcConfig(api_url="http://localhost:1984"),
        temp_manager=TempMediaManager(preferred_temp_dir=str(tmp_path)),
        lookout_config=LookoutConfig(),
    )
    result = await adapter.record_clip("cam_front", 10.0)
    assert result.endswith(".jpg")


async def test_clip_still_rejects_non_mp4_with_audio_enabled(tmp_path: Any) -> None:
    """TR-10: MP4 and sample validation is unchanged with mp4=all present."""
    stub = _Go2RtcStub()
    manager, client = _manager(tmp_path, LookoutConfig(), stub)
    try:
        await manager.start()
        await manager.ensure_stream("cam1", "rtsp://example/cam1")
        assert await manager.clip("cam1", 5) == MP4
        assert _clips(stub)[0].url.params["mp4"] == "all"

        stub.clip_bytes = b"not-an-mp4"
        with pytest.raises(MediaCaptureError):
            await manager.clip("cam1", 5)

        stub.clip_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
        with pytest.raises(MediaCaptureError, match="no media samples"):
            await manager.clip("cam1", 5)
    finally:
        await client.aclose()
