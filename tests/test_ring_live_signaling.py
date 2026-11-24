"""Ring/native live WebRTC signaling routes target the embedded go2rtc."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.ring_native import RingStreamSourceError
from lookout.adapters.web import app as app_module
from lookout.config.schema import CameraConfig, Go2RtcConfig, LookoutConfig

EMBEDDED = "http://127.0.0.1:11984"
EXTERNAL = "http://external-go2rtc:1984"
RING_SOURCE = "ring:?device_id=abc&refresh_token=SECRET123"


class FakeManager:
    def __init__(self) -> None:
        self.api_url = EMBEDDED
        self.streams: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.events: list[str] = []
        self.codecs = ["H264"]

    async def ensure_stream(self, camera_id: str, source: str) -> None:
        self.events.append("ensure")
        self.calls.append((camera_id, source))
        self.streams[camera_id] = source

    async def _stream_exists(self, camera_id: str) -> bool:
        return camera_id in self.streams

    async def stream_codecs(self, camera_id: str) -> list[str]:
        return self.codecs

    async def negotiate_consumer(self, camera_id: str) -> None:
        self.events.append(f"prime:{camera_id}")


class FakeWyzeAdapter:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, str | None]] = []

    def _resolve_source(self, camera_id: str, subtype: str | None = None) -> str:
        self.resolved.append((camera_id, subtype))
        return f"wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x&subtype={subtype}"


class FakeRing:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def _resolve_source(self, camera_id: str, snapshot: bool = False) -> str:
        if self.error:
            raise self.error
        return RING_SOURCE


def _build(error: Exception | None = None) -> tuple[Any, FakeManager]:
    config = LookoutConfig(
        go2rtc=Go2RtcConfig(api_url=EXTERNAL),
        cameras=[
            CameraConfig(
                id="ring_cam",
                name="Ring",
                stream_url="ring:?device_id=abc",
                native_provider="ring",
            ),
            CameraConfig(id="generic", name="Generic", stream_url="rtsp://x/y"),
        ],
    )
    app = app_module.create_app(config=config, repository=InMemoryEventRepository())
    manager = FakeManager()
    app.state.native_stream_manager = manager
    app.state.ring_native = FakeRing(error)
    return app, manager


def _build_wyze() -> tuple[Any, FakeManager, FakeWyzeAdapter]:
    config = LookoutConfig(
        cameras=[
            CameraConfig(
                id="mochi_pan_cam",
                name="Mochi Pan Cam",
                stream_url="wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x&subtype=hd&dtls=true",
                native_provider="wyze",
                native_device_id="m",
            )
        ]
    )
    app = app_module.create_app(config=config, repository=InMemoryEventRepository())
    manager = FakeManager()
    adapter = FakeWyzeAdapter()
    app.state.native_stream_manager = manager
    app.state.wyze_native = adapter
    return app, manager, adapter


def test_wyze_grid_id_registers_sd_source_and_primes_consumer() -> None:
    app, manager, adapter = _build_wyze()
    assert app_module._base_camera_id("mochi_pan_cam_hd") == "mochi_pan_cam"
    assert app_module._stream_subtype("mochi_pan_cam") is None
    assert app_module._stream_subtype("mochi_pan_cam_hd") == "hd"

    assert asyncio.run(app_module._ensure_native_stream(app, "mochi_pan_cam")) is True

    assert adapter.resolved == [("mochi_pan_cam", "sd")]
    assert manager.calls == [
        ("mochi_pan_cam", "wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x&subtype=sd")
    ]
    assert manager.events == ["ensure", "prime:mochi_pan_cam"]


def test_wyze_focus_hd_variant_registers_hd_under_variant_name() -> None:
    app, manager, adapter = _build_wyze()

    assert asyncio.run(app_module._ensure_native_stream(app, "mochi_pan_cam_hd")) is True

    assert adapter.resolved == [("mochi_pan_cam", "hd")]
    assert manager.calls == [
        ("mochi_pan_cam_hd", "wyze://10.0.0.2?uid=u&enr=e&mac=m&model=x&subtype=hd")
    ]
    assert manager.events == ["ensure", "prime:mochi_pan_cam_hd"]
    assert app_module._resolve_go2rtc_target(app, "mochi_pan_cam_hd") == EMBEDDED


def test_native_camera_ws_targets_embedded_go2rtc() -> None:
    app, _ = _build()
    assert app_module._resolve_go2rtc_target(app, "ring_cam") == EMBEDDED
    url = app_module._get_go2rtc_ws_url(
        app, "src=ring_cam", base=app_module._resolve_go2rtc_target(app, "ring_cam")
    )
    assert url.startswith("ws://127.0.0.1:11984/api/ws")
    assert EXTERNAL.split("//")[1] not in url


def test_non_native_camera_ws_targets_configured_go2rtc() -> None:
    app, _ = _build()
    assert app_module._resolve_go2rtc_target(app, "generic") == EXTERNAL
    url = app_module._get_go2rtc_ws_url(
        app, "src=generic", base=app_module._resolve_go2rtc_target(app, "generic")
    )
    assert url.startswith("ws://external-go2rtc:1984/api/ws")


def test_ws_registers_stream_before_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    app, manager = _build()
    seen: list[str] = []

    async def fake_proxy(websocket: Any, upstream_url: str) -> None:
        manager.events.append("proxy")
        seen.append(upstream_url)

    monkeypatch.setattr(app_module, "_proxy_websocket_to_upstream", fake_proxy)
    with TestClient(app) as client, client.websocket_connect("/api/ws?src=ring_cam"):
        pass
    assert manager.calls == [("ring_cam", RING_SOURCE)]
    assert manager.events == ["ensure", "proxy"]
    assert seen[0].startswith("ws://127.0.0.1:11984/api/ws")


def test_ws_closes_cleanly_when_source_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    app, manager = _build(RingStreamSourceError("no device"))
    called: list[str] = []

    async def fake_proxy(websocket: Any, upstream_url: str) -> None:
        called.append(upstream_url)

    monkeypatch.setattr(app_module, "_proxy_websocket_to_upstream", fake_proxy)
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws?src=ring_cam") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_text()
    assert exc.value.code == 1011
    assert called == []
    assert manager.calls == []


def test_live_diagnostics_endpoint_redacts_token() -> None:
    app, manager = _build()
    manager.streams["ring_cam"] = RING_SOURCE
    with TestClient(app) as client:
        resp = client.get("/api/cameras/ring_cam/live/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert {"stream_registered", "source_scheme", "codecs", "h264", "go2rtc_target"} <= set(body)
    assert "SECRET123" not in resp.text
    assert isinstance(body["h264"], bool)
    assert body["h264"] is True
    assert body["source_scheme"] == "ring"


def test_live_diagnostics_redacts_userinfo_and_query_secrets() -> None:
    app, manager = _build()
    manager.streams["ring_cam"] = "rtsp://user:pass@192.168.1.50/stream?refresh_token=abc"
    with TestClient(app) as client:
        resp = client.get("/api/cameras/ring_cam/live/diagnostics")
    body = resp.json()
    assert body["source_scheme"] == "rtsp"
    for secret in ("user", "pass", "abc"):
        assert secret not in resp.text
    assert "192.168.1.50" in body["source"]


def test_live_diagnostics_recommendation_only_for_unhealthy_streams() -> None:
    """Healthy streams must not receive the troubleshooting recommendation."""
    app, manager = _build()
    manager.streams["ring_cam"] = RING_SOURCE

    manager.stream_health = lambda camera_id: {  # type: ignore[attr-defined]
        "status": "ok",
        "classification": "unknown",
        "error": None,
        "consecutive_failures": 0,
        "retry_after_seconds": 0.0,
        "recommendation": None,
    }
    with TestClient(app) as client:
        healthy = client.get("/api/cameras/ring_cam/live/diagnostics").json()
    assert healthy["stream_health"]["status"] == "ok"
    assert healthy["recommendation"] is None

    manager.stream_health = lambda camera_id: {  # type: ignore[attr-defined]
        "status": "unavailable",
        "classification": "unreachable",
        "error": "connect failed: discovery timeout",
        "consecutive_failures": 3,
        "retry_after_seconds": 30.0,
        "recommendation": (
            "Verify native discovery for this camera, check device power/network "
            "reachability, set a per-camera stream_url override, or try "
            "wyze.stream_dtls=false."
        ),
    }
    with TestClient(app) as client:
        unhealthy = client.get("/api/cameras/ring_cam/live/diagnostics").json()
    assert unhealthy["stream_health"]["status"] == "unavailable"
    assert "native discovery" in (unhealthy["recommendation"] or "")


def test_redact_source_redacts_wyze_params() -> None:
    from lookout.adapters.web.app import _redact_source

    src = "wyze://cam?uid=USER123&enr=ENR456&mac=AABBCC&did=DID789&p2p_id=P2P000&width=1920"
    redacted = _redact_source(src)
    assert redacted is not None
    for secret in ("USER123", "ENR456", "AABBCC", "DID789", "P2P000"):
        assert secret not in redacted
    assert "width=1920" in redacted


def test_list_cameras_redacts_stream_url_tokens() -> None:
    app, _ = _build()
    with TestClient(app) as client:
        resp = client.get("/api/cameras")
    assert resp.status_code == 200
    assert "SECRET123" not in resp.text


def test_ring_resolve_source_uses_h264_bridge_for_live_stream() -> None:
    from lookout.adapters.ring_native import RingNativeAdapter
    from lookout.config import CameraConfig, LookoutConfig

    cam = CameraConfig(
        id="ring_front",
        name="Front",
        provider="ring",
        native_device_id="123",
        stream_url="ring:?camera_id=123&device_id=abc&refresh_token=tok&snapshot",
    )
    adapter = RingNativeAdapter(config=LookoutConfig(cameras=[cam]))
    live_source = adapter._resolve_source("ring_front", snapshot=False)
    assert live_source.startswith("exec:node ")
    assert "ring_bridge/index.mjs" in live_source
    assert "--camera-id 123" in live_source
    assert "--refresh-token tok" in live_source
    assert adapter._resolve_source("ring_front", snapshot=True) == (
        "ring:?camera_id=123&device_id=abc&refresh_token=tok&snapshot"
    )


def test_redact_source_redacts_bridge_refresh_token() -> None:
    src = (
        "exec:node /app/src/lookout/adapters/ring_bridge/index.mjs "
        "--camera-id 123 --refresh-token SECRET123"
    )
    redacted = app_module._redact_source(src)
    assert redacted is not None
    assert "SECRET123" not in redacted
    assert "--refresh-token [redacted]" in redacted
