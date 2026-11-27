"""Unit and integration tests for the Wyze Pan Scan toggle (#70)."""

import ast
import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.adapters.web.helpers import build_camera_card_data
from lookout.adapters.wyze_iot_cloud import (
    IotPropertyResult,
    WyzeIotCloudAdapter,
    WyzeProtocolError,
)
from lookout.adapters.wyze_pan_scan import WyzePanScanAdapter
from lookout.adapters.wyze_signing import WyzeSigner
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig, WyzeConfig
from lookout.domain.models import PanScanState, PanScanWriteResult
from lookout.domain.ptz_service import PtzControlService
from lookout.ports.egress import PanScanPort


def _run(coro):
    return asyncio.run(coro)


def _wyze_camera(**overrides) -> CameraConfig:
    data = {
        "id": "cam1",
        "name": "Cam 1",
        "stream_url": "wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_PAN3&subtype=hd",
        "native_provider": "wyze",
        "native_device_id": "AA:BB:CC:DD:EE:FF",
    }
    data.update(overrides)
    return CameraConfig(**data)


class FakeIotCloud:
    """Records envelopes and serves a configurable capability response."""

    def __init__(
        self,
        *,
        payload: dict | None = None,
        supported: bool = True,
        iid: int = 9,
        capability_iids: dict | None = None,
        map_known: bool = False,
    ):
        self.payload = dict(payload or {})
        self.supported = supported
        self.iid = iid
        self.capability_iids = dict(capability_iids or {})
        self.map_known = map_known
        self.read_calls: list[dict] = []
        self.write_bodies: list[dict] = []
        self.value = self.payload.get("camera-pnt::scan")

    async def get_iot_prop(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capabilities=None,
        properties=None,
        capability_iids=None,
    ):
        self.read_calls.append(
            {
                "device_id": device_id,
                "product_model": product_model,
                "user_id": user_id,
                "capabilities": list(capabilities or []),
                "properties": list(properties or []),
                "capability_iids": dict(capability_iids or {}),
            }
        )
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability="camera-pnt",
            properties=dict(self.payload) if self.supported else {},
            raw={"code": 1},
            capability_iid=self.iid if self.supported else None,
            capability_iids=dict(self.capability_iids),
            capability_map_known=self.map_known,
        )

    async def run_action(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capability="camera-pnt",
        properties=None,
        capability_iid=None,
    ):
        capability_entry = {"name": capability, "properties": list(properties or [])}
        if capability_iid is not None:
            capability_entry["iid"] = capability_iid
        self.write_bodies.append(
            {
                "targetInfo": {
                    "id": device_id,
                    "productModel": product_model,
                    "type": "DEVICE",
                },
                "userId": user_id,
                "capabilities": [capability_entry],
            }
        )
        for prop in properties or []:
            if prop.get("prop") == "camera-pnt::scan":
                self.value = bool(prop.get("value"))
        return {"status": "ok", "response": {"code": 1}}


def _adapter(iot, **kwargs) -> WyzePanScanAdapter:
    kwargs.setdefault("device_resolver", lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"))
    return WyzePanScanAdapter(iot_cloud=iot, **kwargs)


def _build_app(tmp_path, adapter) -> tuple[LookoutConfig, Path, TestClient]:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_wyze_camera()],
    )
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    app.state.ptz_service = PtzControlService(
        cameras=config.cameras,
        pan_scan_adapter=adapter,
        config=config,
        config_path=config_path,
    )
    return config, config_path, TestClient(app)


# ----------------------------------------------------------------------
# TR-1: tri-state config
# ----------------------------------------------------------------------
def test_pan_scan_config_is_tri_state():
    default = CameraConfig(id="pan1", name="Pan 1", stream_url="rtsp://localhost/pan1")
    assert default.pan_scan_enabled is None

    enabled = CameraConfig(
        id="pan1", name="Pan 1", stream_url="rtsp://localhost/pan1", pan_scan_enabled=True
    )
    assert enabled.pan_scan_enabled is True
    assert "pan_scan_enabled" in enabled.model_dump()


# ----------------------------------------------------------------------
# TR-2..TR-5: adapter behavior
# ----------------------------------------------------------------------
def test_pan_scan_adapter_returns_supported_state_for_pan_camera():
    iot = FakeIotCloud(payload={"camera-pnt::scan": True, "camera-pnt::tracking": False})
    state = _run(_adapter(iot).get_pan_scan("cam1"))
    assert state == PanScanState(supported=True, enabled=True, tracking=False)


def test_pan_scan_adapter_reports_fixed_camera_unsupported_without_write():
    iot = FakeIotCloud(supported=False)
    adapter = _adapter(iot)

    state = _run(adapter.get_pan_scan("cam1"))
    assert state == PanScanState(supported=False, enabled=None, tracking=None)

    written = _run(adapter.set_pan_scan("cam1", True))
    assert written == PanScanWriteResult(supported=False, enabled=None, raw={})
    assert iot.write_bodies == []


def test_pan_scan_adapter_known_capability_map_without_camera_pnt_is_unsupported():
    iot = FakeIotCloud(
        payload={"camera-pnt::scan": True},
        capability_iids={"motion-detection": 7},
        map_known=True,
    )
    state = _run(_adapter(iot).get_pan_scan("cam1"))
    assert state.supported is False


def test_pan_scan_write_forwards_cached_capability_iid():
    iot = FakeIotCloud(payload={"camera-pnt::scan": False}, iid=9)
    adapter = _adapter(iot)
    _run(adapter.get_pan_scan("cam1"))
    result = _run(adapter.set_pan_scan("cam1", True))
    assert result.supported is True
    assert iot.write_bodies[0]["capabilities"][0]["iid"] == 9
    assert iot.write_bodies[0]["capabilities"][0]["name"] == "camera-pnt"


# ----------------------------------------------------------------------
# TR-6/TR-7: golden envelopes (mocked transport, literal test token only)
# ----------------------------------------------------------------------
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
        self.calls.append({"url": url, "content": content, "json": json, "headers": headers or {}})
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


def test_pan_scan_write_envelope_uses_camera_pnt_capability():
    client = _MockClient(
        [
            _Resp(
                200,
                {
                    "code": 1,
                    "data": {
                        "capabilities": [
                            {
                                "name": "camera-pnt",
                                "iid": 9,
                                "properties": [{"prop": "camera-pnt::scan", "value": False}],
                            }
                        ]
                    },
                },
            ),
            _Resp(200, {"code": 1}),
        ]
    )
    adapter = WyzePanScanAdapter(
        iot_cloud=_golden_iot(client),
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"),
        user_id="u1",
    )
    result = _run(adapter.set_pan_scan("pan1", True))
    assert result.supported is True and result.enabled is True
    write_body = json.loads(client.calls[1]["content"])
    assert write_body["capabilities"][0]["properties"][0] == {
        "separator": "::",
        "prop": "camera-pnt::scan",
        "value": True,
    }


def test_pan_scan_read_envelope_requests_scan_and_tracking():
    client = _MockClient(
        [
            _Resp(
                200,
                {
                    "code": 1,
                    "data": {
                        "capabilities": [
                            {
                                "name": "camera-pnt",
                                "iid": 9,
                                "properties": [
                                    {"prop": "camera-pnt::scan", "value": True},
                                    {"prop": "camera-pnt::tracking", "value": False},
                                ],
                            }
                        ]
                    },
                },
            )
        ]
    )
    adapter = WyzePanScanAdapter(
        iot_cloud=_golden_iot(client),
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3"),
        user_id="u1",
    )
    state = _run(adapter.get_pan_scan("pan1"))
    assert state.supported is True
    read_body = json.loads(client.calls[0]["content"])
    assert read_body["capabilities"][0]["properties"] == [
        {"separator": "::", "prop": "camera-pnt::scan"},
        {"separator": "::", "prop": "camera-pnt::tracking"},
    ]
    assert read_body["capabilities"][0]["name"] == "camera-pnt"
    assert "signature2" in client.calls[0]["headers"]
    assert "test-token" not in client.calls[0]["content"].decode()


# ----------------------------------------------------------------------
# TR-20: legacy get_iot_prop default envelope
# ----------------------------------------------------------------------
def test_get_iot_prop_default_property_name_stays_motion_tag():
    client = _MockClient([_Resp(200, {"code": 1, "data": {}})])
    iot = _golden_iot(client)
    _run(iot.get_iot_prop("dev1", "HL_CAM4", user_id="u1"))
    body = json.loads(client.calls[0]["content"])
    assert body["capabilities"][0]["properties"] == [
        {"separator": "::", "prop": "motion-detection::motion-tag"}
    ]


# ----------------------------------------------------------------------
# TR-8..TR-11: domain service + REST routes
# ----------------------------------------------------------------------
class _ProtocolFailingAdapter:
    async def get_pan_scan(self, camera_id):
        raise WyzeProtocolError("Wyze request failed: HTTP 400")

    async def set_pan_scan(self, camera_id, enabled):
        raise WyzeProtocolError("Wyze request failed: HTTP 400")


def test_pan_scan_get_route_reports_unsupported_for_fixed_camera(tmp_path):
    iot = FakeIotCloud(supported=False)
    config, _config_path, client = _build_app(tmp_path, _adapter(iot))

    response = client.get("/api/cameras/cam1/pan_scan")
    assert response.status_code == 200
    assert response.json() == {"supported": False, "enabled": None, "tracking": None}
    assert config.cameras[0].pan_scan_enabled is None


def test_pan_scan_post_route_returns_501_for_unsupported_camera(tmp_path):
    iot = FakeIotCloud(supported=False)
    config, _config_path, client = _build_app(tmp_path, _adapter(iot))

    response = client.post("/api/cameras/cam1/pan_scan", json={"enabled": True})
    assert response.status_code == 501
    assert config.cameras[0].pan_scan_enabled is None
    assert iot.write_bodies == []


def test_pan_scan_toggle_round_trips_and_persists_config(tmp_path):
    iot = FakeIotCloud(payload={"camera-pnt::scan": False, "camera-pnt::tracking": False})
    config, config_path, client = _build_app(tmp_path, _adapter(iot))

    read = client.get("/api/cameras/cam1/pan_scan")
    assert read.json() == {"supported": True, "enabled": False, "tracking": False}

    written = client.post("/api/cameras/cam1/pan_scan", json={"enabled": True})
    assert written.status_code == 200
    assert written.json()["enabled"] is True
    assert iot.value is True
    assert config.cameras[0].pan_scan_enabled is True
    persisted = yaml.safe_load(config_path.read_text())["cameras"][0]["pan_scan_enabled"]
    assert persisted is True

    after = client.get("/api/cameras/cam1/pan_scan")
    assert after.json()["enabled"] is True


async def test_pan_scan_set_audits_success():
    audit_logger = AuditLogger(db_path=":memory:")
    adapter = _adapter(FakeIotCloud(payload={"camera-pnt::scan": False}))
    service = PtzControlService(
        cameras=[_wyze_camera()],
        pan_scan_adapter=adapter,
        audit_logger=audit_logger,
    )
    result = await service.set_pan_scan("cam1", True)
    assert result.supported is True
    entries = await audit_logger.query()
    assert entries[0]["action"] == "pan_scan_set"


def test_pan_scan_get_route_returns_502_on_protocol_error(tmp_path):
    config, _config_path, client = _build_app(tmp_path, _ProtocolFailingAdapter())
    before = config.cameras[0].pan_scan_enabled

    response = client.get("/api/cameras/cam1/pan_scan")
    assert response.status_code == 502
    assert config.cameras[0].pan_scan_enabled == before


# ----------------------------------------------------------------------
# TR-12: port conformance
# ----------------------------------------------------------------------
def test_pan_scan_adapter_implements_port():
    adapter = WyzePanScanAdapter(
        iot_cloud=FakeIotCloud(), device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_PAN3")
    )
    assert isinstance(adapter, PanScanPort) is True


# ----------------------------------------------------------------------
# TR-16: card data
# ----------------------------------------------------------------------
def test_camera_card_exposes_pan_scan_state():
    cam = _wyze_camera(pan_scan_enabled=True)
    assert build_camera_card_data(cam)["pan_scan_enabled"] is True


# ----------------------------------------------------------------------
# Architecture
# ----------------------------------------------------------------------
def test_pan_scan_adapter_is_not_imported_by_domain():
    domain_dir = Path("src/lookout/domain")
    imported_modules: list[str] = []
    for py_file in domain_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
    assert "lookout.adapters.wyze_pan_scan" not in imported_modules


@pytest.mark.parametrize("value", ["1", "true", "on", True])
def test_pan_scan_adapter_coerces_truthy_strings(value):
    iot = FakeIotCloud(payload={"camera-pnt::scan": value})
    state = _run(_adapter(iot).get_pan_scan("cam1"))
    assert state.supported is True
    assert state.enabled is True


def test_pan_scan_adapter_treats_string_zero_as_disabled():
    iot = FakeIotCloud(payload={"camera-pnt::scan": "0"})
    state = _run(_adapter(iot).get_pan_scan("cam1"))
    assert state.supported is True
    assert state.enabled is False
