"""Unit tests for FastAPI WebRTC WebSocket and HTTP proxy endpoints to go2rtc."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from lookout.adapters.web.app import _prepare_live_upstream, create_app
from lookout.config.schema import CameraConfig, Go2RtcConfig, LookoutConfig


@pytest.fixture
def configured_app() -> TestClient:
    config = LookoutConfig(
        system_name="Test Lookout",
        go2rtc=Go2RtcConfig(api_url="http://127.0.0.1:1984"),
    )
    app = create_app(config=config)
    return TestClient(app)


def test_webrtc_websocket_proxy_bidirectional(configured_app: TestClient) -> None:
    import asyncio
    from unittest.mock import MagicMock

    mock_upstream_ws = AsyncMock()
    mock_upstream_ws.send_str = AsyncMock()

    async def msg_generator():
        msg = MagicMock()
        msg.type = aiohttp.WSMsgType.TEXT
        msg.data = '{"type":"webrtc/answer","value":"mock_sdp"}'
        yield msg
        await asyncio.sleep(0.5)

    mock_upstream_ws.__aiter__.side_effect = msg_generator

    mock_session = MagicMock()
    mock_session.ws_connect.return_value.__aenter__.return_value = mock_upstream_ws
    mock_session.__aenter__.return_value = mock_session

    with (
        patch("aiohttp.ClientSession", return_value=mock_session),
        configured_app.websocket_connect("/api/ws?src=front-porch") as ws,
    ):
        ws.send_text('{"type":"webrtc/offer","value":"mock_offer"}')
        received = ws.receive_text()
        assert "mock_sdp" in received
        mock_upstream_ws.send_str.assert_awaited()


def test_webrtc_camera_specific_websocket_proxy(configured_app: TestClient) -> None:
    import asyncio
    from unittest.mock import MagicMock

    mock_upstream_ws = AsyncMock()
    mock_upstream_ws.send_str = AsyncMock()

    async def msg_generator():
        msg = MagicMock()
        msg.type = aiohttp.WSMsgType.TEXT
        msg.data = '{"type":"webrtc/answer","value":"mock_camera_sdp"}'
        yield msg
        await asyncio.sleep(0.5)

    mock_upstream_ws.__aiter__.side_effect = msg_generator

    mock_session = MagicMock()
    mock_session.ws_connect.return_value.__aenter__.return_value = mock_upstream_ws
    mock_session.__aenter__.return_value = mock_session

    with (
        patch("aiohttp.ClientSession", return_value=mock_session),
        configured_app.websocket_connect("/api/webrtc/backyard/ws") as ws,
    ):
        ws.send_text('{"type":"webrtc/offer","value":"camera_offer"}')
        received = ws.receive_text()
        assert "mock_camera_sdp" in received


async def test_live_signaling_primes_wyze_consumer_before_upstream() -> None:
    """TR-13: Wyze primes a consumer before the upstream WS URL is built; Ring does not."""
    config = LookoutConfig(
        cameras=[
            CameraConfig(
                id="mochi",
                name="Mochi",
                stream_url="wyze://192.168.1.50?uid=u&enr=e&mac=m&model=x",
                native_provider="wyze",
                native_device_id="m",
            ),
            CameraConfig(
                id="front_door",
                name="Front Door",
                stream_url="ring:?camera_id=1&device_id=abc",
                native_provider="ring",
                native_device_id="1",
            ),
        ]
    )
    app = create_app(config=config)
    calls: list[tuple[str, ...]] = []

    class FakeManager:
        api_url = "http://127.0.0.1:11984"
        streams = {
            "mochi": "wyze://192.168.1.50?uid=u&enr=e&mac=m&model=x",
            "front_door": "ring:?camera_id=1&device_id=abc",
        }

        async def ensure_stream(self, camera_id: str, source: str | None = None) -> None:
            calls.append(("ensure", camera_id))

        async def negotiate_consumer(self, camera_id: str) -> None:
            calls.append(("negotiate", camera_id))

    class FakeWyze:
        def __init__(self) -> None:
            self.subtypes: list[str | None] = []

        def _resolve_source(self, camera_id: str, subtype: str | None = None) -> str:
            self.subtypes.append(subtype)
            return "wyze://192.168.1.50?uid=u&enr=e&mac=m&model=x"

    class FakeRing:
        def _resolve_source(self, camera_id: str, snapshot: bool = False) -> str:
            return "ring:?camera_id=1&device_id=abc"

    fake_wyze = FakeWyze()
    app.state.native_stream_manager = FakeManager()
    app.state.wyze_native = fake_wyze
    app.state.ring_native = FakeRing()

    class FakeWS:
        async def close(self, code: int | None = None) -> None:
            return None

    def fake_upstream(*args: object, **kwargs: object) -> str:
        calls.append(("upstream",))
        return "ws://upstream/api/ws"

    with patch("lookout.adapters.web.app._get_go2rtc_ws_url", side_effect=fake_upstream):
        url = await _prepare_live_upstream(app, FakeWS(), "mochi", "src=mochi")
    assert url == "ws://upstream/api/ws"
    assert fake_wyze.subtypes == ["sd"]
    assert calls.index(("negotiate", "mochi")) < calls.index(("upstream",))

    calls.clear()
    with patch("lookout.adapters.web.app._get_go2rtc_ws_url", side_effect=fake_upstream):
        await _prepare_live_upstream(app, FakeWS(), "front_door", "src=front_door")
    assert ("negotiate", "front_door") not in calls


def test_live_diagnostics_reports_stream_health_for_unavailable_camera() -> None:
    """TR-14: live diagnostics exposes redacted, actionable stream health."""
    config = LookoutConfig(
        cameras=[
            CameraConfig(
                id="office_cam",
                name="Office Cam",
                stream_url="wyze://192.168.1.222?uid=SECRETUID&enr=e&mac=2CAA8E1EB2C9&model=x",
                native_provider="wyze",
                native_device_id="2CAA8E1EB2C9",
            )
        ]
    )
    app = create_app(config=config)

    class FakeManager:
        api_url = None
        streams = {
            "office_cam": ("wyze://192.168.1.222?uid=SECRETUID&enr=e&mac=2CAA8E1EB2C9&model=x")
        }

        def stream_health(self, camera_id: str) -> dict[str, object]:
            return {
                "status": "unavailable",
                "classification": "unreachable",
                "error": "connect failed: discovery timeout uid=[redacted]",
                "consecutive_failures": 12,
                "retry_after_seconds": 30.0,
            }

    app.state.native_stream_manager = FakeManager()
    client = TestClient(app)
    response = client.get("/api/cameras/office_cam/live/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["h264"] is False
    assert data["stream_health"]["classification"] == "unreachable"
    assert "SECRETUID" not in str(data["error"])
    assert "native discovery" in data["recommendation"]


def test_webrtc_http_post_proxy(configured_app: TestClient) -> None:
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.content = b"v=0\r\no=- 0 0 IN IP4 127.0.0.1"
    fake_resp.headers = {"content-type": "application/sdp"}

    mock_client = AsyncMock()
    mock_client.post.return_value = fake_resp

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value = mock_client
        resp = configured_app.post(
            "/api/webrtc?src=front-porch",
            content="v=0\r\no=- 0 0 IN IP4 127.0.0.1",
            headers={"content-type": "application/sdp"},
        )
        assert resp.status_code == 200
        assert "v=0" in resp.text
