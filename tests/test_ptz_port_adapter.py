"""Unit tests for PTZ port and adapter implementations."""

import httpx
import pytest
import respx

from lookout.adapters.go2rtc_ptz import Go2RtcPtzAdapter
from lookout.adapters.memory.ptz import MockPtzControlAdapter
from lookout.config.schema import Go2RtcConfig
from lookout.ports.egress import PtzControlPort


def test_go2rtc_ptz_adapter_implements_port() -> None:
    """TR-1: Go2RtcPtzAdapter implements PtzControlPort."""
    assert isinstance(Go2RtcPtzAdapter(), PtzControlPort)


def test_mock_ptz_adapter_implements_port() -> None:
    """TR-2: MockPtzControlAdapter implements PtzControlPort."""
    assert isinstance(MockPtzControlAdapter(), PtzControlPort)


@pytest.mark.asyncio
@respx.mock
async def test_ptz_wyze_move_calls_bridge_endpoint() -> None:
    """TR-3: Wyze move calls bridge endpoint via POST."""
    adapter = Go2RtcPtzAdapter(bridge_url="http://wyze-bridge:8080")
    respx.post("http://wyze-bridge:8080/api/cameras/cam1/ptz").respond(
        status_code=200, json={"ok": True}
    )

    await adapter.move("cam1", 0.1, 0.2, 0.3, 0.5)

    assert respx.calls.last.request.method == "POST"


@pytest.mark.asyncio
@respx.mock
async def test_ptz_onvif_goto_preset_calls_go2rtc() -> None:
    """TR-4: ONVIF goto preset calls go2rtc and returns 200."""
    adapter = Go2RtcPtzAdapter(config=Go2RtcConfig(api_url="http://go2rtc:1984"))
    # Wyze path unsupported-ish (non-501) to force fallback to ONVIF
    respx.post("http://go2rtc:1984/api/cameras/cam1/ptz").respond(status_code=404)
    # ONVIF candidate
    respx.post("http://go2rtc:1984/api/onvif/ptz?src=cam1").respond(
        status_code=200, json={"ok": True}
    )

    response = await adapter.goto_preset("cam1", "home", 0.5)

    assert response.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_ptz_unsupported_returns_501() -> None:
    """TR-5: Unsupported camera returns 501 propagating HTTPStatusError."""
    adapter = Go2RtcPtzAdapter(bridge_url="http://wyze-bridge:8080")
    respx.post("http://wyze-bridge:8080/api/cameras/cam1/ptz").respond(status_code=501)

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.move("cam1", 0.1, 0.2, 0.3, 0.5)
