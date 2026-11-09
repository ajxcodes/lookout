"""Unit tests for FastAPI WebRTC WebSocket and HTTP proxy endpoints to go2rtc."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from lookout.adapters.web.app import create_app
from lookout.config.schema import Go2RtcConfig, LookoutConfig


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
