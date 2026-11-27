"""Tests for the Lane-B ``camera-position::*`` PTZ adapter (#71)."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.adapters.wyze_agora_ptz import (
    CloudCameraPositionTransport,
    DataChannelCameraPositionTransport,
    WyzeAgoraPtzAdapter,
    WyzeCameraPositionError,
)
from lookout.adapters.wyze_iot_cloud import WyzeIotCloudAdapter
from lookout.adapters.wyze_native import (
    WyzeNativeAdapter,
    WyzeTutkPtzAdapter,
    build_wyze_ptz_adapter,
)
from lookout.adapters.wyze_signing import WyzeSigner
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig, WyzeConfig
from lookout.domain.ptz_service import PtzControlService
from lookout.ports.egress import PtzControlPort


def _run(coro):
    return asyncio.run(coro)


def _wyze_camera(
    *,
    ptz_enabled: bool = True,
    ptz_protocol: str = "native-wyze",
    native_provider: str = "wyze",
    native_device_id: str = "AA:BB:CC:DD:EE:FF",
    native_model: str = "HL_PAN3",
) -> CameraConfig:
    return CameraConfig(
        id="cam1",
        name="Cam 1",
        stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_PAN3&subtype=hd",
        native_provider=native_provider,
        native_device_id=native_device_id,
        native_model=native_model,
        ptz_enabled=ptz_enabled,
        ptz_protocol=ptz_protocol,
    )


class FakeTransport:
    """Records ``run_action`` calls and serves queued payloads."""

    def __init__(self, payloads=None):
        self.calls: list[tuple[str, str, dict]] = []
        self._payloads = list(payloads or [])

    async def run_action(self, camera_id, action, params=None):
        self.calls.append((str(camera_id), action, dict(params or {})))
        if self._payloads:
            return self._payloads.pop(0)
        return {"result": 0}


class FakeIotCloud:
    """Minimal stand-in for ``WyzeIotCloudAdapter`` used by the factory."""

    def __init__(self, *, user_id="u1"):
        self.user_id = user_id
        self.calls: list[dict] = []

    async def run_action(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capability=None,
        properties=None,
        capability_iid=None,
    ):
        self.calls.append(
            {
                "device_id": device_id,
                "product_model": product_model,
                "user_id": user_id,
                "capability": capability,
                "properties": properties,
            }
        )
        return {"status": "ok", "response": {"code": 1}}


def _lane_b(transport) -> WyzeAgoraPtzAdapter:
    return WyzeAgoraPtzAdapter(transport=transport)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _MockClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def post(self, url, content=None, headers=None, json=None):
        self.calls.append({"url": url, "content": content, "headers": headers or {}})
        if self._responses:
            return self._responses.pop(0)
        return _Resp(200, {})


def _golden_iot(client) -> WyzeIotCloudAdapter:
    cfg = WyzeConfig(auth_token="test-token", device_management_base_url="https://example.test")
    return WyzeIotCloudAdapter(
        config=cfg,
        http_client=client,
        signer=WyzeSigner(access_token="test-token"),
        user_id="u1",
    )


def _build_app(tmp_path: Path, adapter) -> TestClient:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_wyze_camera()],
    )
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    app.state.ptz_service = PtzControlService(
        cameras=config.cameras,
        ptz_adapters={"cam1": adapter},
        config=config,
        config_path=config_path,
    )
    return TestClient(app)


# ----------------------------------------------------------------------
# TR-1..TR-6: flag, factory, and app wiring
# ----------------------------------------------------------------------
def test_agora_ptz_config_flag_defaults_off():
    assert WyzeConfig().agora_ptz is False
    assert WyzeConfig(agora_ptz=True).agora_ptz is True


def test_factory_defaults_to_tutk_adapter():
    adapter = build_wyze_ptz_adapter(WyzeConfig(), client=object())
    assert isinstance(adapter, WyzeTutkPtzAdapter) is True


def test_factory_selects_lane_b_when_flag_enabled():
    adapter = build_wyze_ptz_adapter(
        WyzeConfig(agora_ptz=True), client=object(), iot_cloud=FakeIotCloud()
    )
    assert type(adapter).__name__ == "WyzeAgoraPtzAdapter"


def test_native_adapter_uses_lane_b_only_when_flag_enabled():
    off = WyzeNativeAdapter(config=WyzeConfig())
    on = WyzeNativeAdapter(config=WyzeConfig(agora_ptz=True))
    assert type(off._ptz).__name__ == "WyzeTutkPtzAdapter"
    assert type(on._ptz).__name__ == "WyzeAgoraPtzAdapter"


def test_create_app_keeps_tutk_ptz_adapter_by_default(tmp_path):
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_wyze_camera()],
    )
    app = create_app(config=config, config_path=tmp_path / "config.yaml")
    assert type(app.state.ptz_service.ptz_adapters["cam1"]).__name__ == "WyzeTutkPtzAdapter"


def test_create_app_wires_lane_b_ptz_adapter_when_flag_enabled(tmp_path):
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_wyze_camera()],
        wyze=WyzeConfig(agora_ptz=True),
    )
    app = create_app(config=config, config_path=tmp_path / "config.yaml")
    assert type(app.state.ptz_service.ptz_adapters["cam1"]).__name__ == "WyzeAgoraPtzAdapter"


# ----------------------------------------------------------------------
# TR-7..TR-9: golden cloud envelope
# ----------------------------------------------------------------------
def test_step_issues_move_position_cloud_envelope():
    client = _MockClient([_Resp(200, {"code": 1})])
    adapter = _lane_b(
        CloudCameraPositionTransport(
            iot_cloud=_golden_iot(client),
            device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"),
        )
    )
    _run(adapter.step("cam1", "left"))
    body = json.loads(client.calls[0]["content"])
    capability = body["capabilities"][0]
    assert capability["name"] == "camera-position"
    assert capability["iid"] is None
    assert capability["properties"][0] == {
        "separator": "::",
        "prop": "camera-position::move-position",
        "value": {"direction": "left", "step": 1000},
    }
    assert client.calls[0]["url"].endswith("/device-management/api/action/run_action")


def test_stop_issues_stop_move_position_cloud_envelope():
    client = _MockClient([_Resp(200, {"code": 1})])
    adapter = _lane_b(
        CloudCameraPositionTransport(
            iot_cloud=_golden_iot(client),
            device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"),
        )
    )
    _run(adapter.stop("cam1"))
    body = json.loads(client.calls[0]["content"])
    assert body["capabilities"][0]["properties"][0] == {
        "separator": "::",
        "prop": "camera-position::stop-move-position",
        "value": {},
    }


def test_step_envelope_carries_signature_and_no_token():
    client = _MockClient([_Resp(200, {"code": 1})])
    adapter = _lane_b(
        CloudCameraPositionTransport(
            iot_cloud=_golden_iot(client),
            device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"),
        )
    )
    _run(adapter.step("cam1", "left"))
    assert "signature2" in client.calls[0]["headers"]
    assert b"test-token" not in client.calls[0]["content"]


# ----------------------------------------------------------------------
# TR-10..TR-11: data-channel envelope
# ----------------------------------------------------------------------
def test_data_channel_step_envelope_is_protocol_exact():
    sent: list[dict] = []

    async def sender(camera_id, envelope):
        sent.append({"camera_id": camera_id, "envelope": envelope})
        return {"result": 0}

    adapter = _lane_b(DataChannelCameraPositionTransport(send=sender))
    _run(adapter.step("cam1", "left"))
    envelope = sent[0]["envelope"]
    assert sent[0]["camera_id"] == "cam1"
    assert envelope["cmd"] == "run_action"
    assert envelope["action"] == "camera-position::move-position"
    assert envelope["params"] == {"direction": "left", "step": 1000}
    assert isinstance(envelope["tid"], int) and envelope["tid"] >= 1
    assert isinstance(envelope["ts"], int) and envelope["ts"] > 0


def test_data_channel_nonzero_result_raises_502():
    async def sender(camera_id, envelope):
        return {"result": 1}

    adapter = _lane_b(DataChannelCameraPositionTransport(send=sender))
    with pytest.raises(WyzeCameraPositionError) as excinfo:
        _run(adapter.step("cam1", "left"))
    assert excinfo.value.status_code == 502


# ----------------------------------------------------------------------
# TR-12..TR-15: get_position decoding and normalisation
# ----------------------------------------------------------------------
def test_get_position_normalizes_to_unit_range():
    transport = FakeTransport([{"result": 0, "params": {"horizontal": -90, "vertical": 45}}])
    position = _run(_lane_b(transport).get_position("cam1"))
    assert position["horizontal"] == -1.0
    assert position["vertical"] == 0.5
    assert position["source"] == "camera-position::get-position"


def test_get_position_clamps_out_of_range_values():
    transport = FakeTransport([{"horizontal": 180, "vertical": -180}])
    position = _run(_lane_b(transport).get_position("cam1"))
    assert position["horizontal"] == 1.0
    assert position["vertical"] == -1.0


def test_get_position_reads_transport_on_every_call():
    transport = FakeTransport(
        [
            {"result": 0, "params": {"horizontal": -45, "vertical": 0}},
            {"result": 0, "params": {"horizontal": 90, "vertical": 90}},
        ]
    )
    adapter = _lane_b(transport)
    first = _run(adapter.get_position("cam1"))
    second = _run(adapter.get_position("cam1"))
    assert (first["horizontal"], first["vertical"]) == (-0.5, 0.0)
    assert (second["horizontal"], second["vertical"]) == (1.0, 1.0)
    assert len(transport.calls) == 2


def test_get_position_without_payload_raises_502():
    transport = FakeTransport([{"result": 0}])
    with pytest.raises(WyzeCameraPositionError) as excinfo:
        _run(_lane_b(transport).get_position("cam1"))
    assert excinfo.value.status_code == 502


# ----------------------------------------------------------------------
# TR-16..TR-19: command mapping
# ----------------------------------------------------------------------
def test_move_sends_absolute_set_position():
    transport = FakeTransport()
    _run(_lane_b(transport).move("cam1", 0.5, -0.5, 0.0, 0.5))
    assert transport.calls == [
        ("cam1", "camera-position::set-position", {"horizontal": 45, "vertical": -45})
    ]


def test_zoom_only_and_unknown_direction_are_unsupported():
    transport = FakeTransport()
    adapter = _lane_b(transport)
    assert _run(adapter.move("cam1", 0.0, 0.0, 0.5))["status"] == "unsupported"
    assert _run(adapter.step("cam1", "sideways"))["status"] == "unsupported"
    assert transport.calls == []


def test_goto_preset_maps_numeric_index_and_home():
    transport = FakeTransport()
    adapter = _lane_b(transport)
    _run(adapter.goto_preset("cam1", "2"))
    _run(adapter.goto_preset("cam1", "home"))
    assert transport.calls == [
        ("cam1", "camera-position::goto-waypoint", {"home": False, "index": 2}),
        ("cam1", "camera-position::goto-waypoint", {"home": True, "index": 0}),
    ]


def test_preset_write_and_list_are_explicitly_unsupported():
    transport = FakeTransport()
    adapter = _lane_b(transport)
    assert _run(adapter.set_preset("cam1", "2"))["status"] == "unsupported"
    listed = _run(adapter.list_presets("cam1"))
    assert listed["status"] == "unsupported"
    assert listed["presets"] == []
    assert _run(adapter.goto_preset("cam1", "gate"))["status"] == "unsupported"
    assert transport.calls == []


# ----------------------------------------------------------------------
# TR-20..TR-22: port conformance, service audit, and route wiring
# ----------------------------------------------------------------------
def test_lane_b_adapter_implements_ptz_port():
    adapter = _lane_b(FakeTransport())
    assert isinstance(adapter, PtzControlPort) is True
    _run(adapter.step("cam1", "left"))
    assert adapter.last_command == "step"
    assert adapter.last_speed == pytest.approx(0.5)


async def test_service_step_audits_and_routes_through_lane_b_adapter():
    audit_logger = AuditLogger(db_path=":memory:")
    transport = FakeTransport()
    service = PtzControlService(
        cameras=[_wyze_camera()],
        ptz_adapters={"cam1": _lane_b(transport)},
        audit_logger=audit_logger,
    )
    await service.step("cam1", "left")
    entries = await audit_logger.query()
    assert entries[0]["action"] == "ptz_step"
    assert transport.calls[0][1] == "camera-position::move-position"


def test_ptz_route_step_reaches_lane_b_adapter(tmp_path):
    transport = FakeTransport()
    client = _build_app(tmp_path, _lane_b(transport))
    response = client.post(
        "/api/cameras/cam1/ptz",
        json={"action": "step", "direction": "left"},
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code == 200
    assert transport.calls[0][1] == "camera-position::move-position"
    assert response.json()["status"] == "ok"
