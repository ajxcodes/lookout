"""Unit and integration tests for the Wyze cloud motion-tag toggle (#69)."""

import ast
import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.adapters.wyze_iot_cloud import (
    DEFAULT_CAPABLE_MOTION_TAG_MODELS,
    IotPropertyResult,
    WyzeIotCloudAdapter,
    WyzeMotionTagAdapter,
    WyzeProtocolError,
)
from lookout.adapters.wyze_signing import WyzeSigner
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig, WyzeConfig
from lookout.domain.ptz_service import PtzControlService
from lookout.ports.egress import MotionTagPort


def _run(coro):
    return asyncio.run(coro)


def _wyze_camera(**overrides) -> CameraConfig:
    data = {
        "id": "cam1",
        "name": "Cam 1",
        "stream_url": "wyze://?mac=AA:BB:CC:DD:EE:FF&model=WYZE_CA&subtype=hd",
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
        supported: bool = True,
        value: bool = True,
        iid: int = 7,
        capability_iids: dict | None = None,
        map_known: bool = False,
    ):
        self.supported = supported
        self.value = value
        self.iid = iid
        self.capability_iids = dict(capability_iids or {})
        self.map_known = map_known
        self.read_calls: list[dict] = []
        self.write_bodies: list[dict] = []

    async def get_iot_prop(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capabilities=None,
        capability_iids=None,
    ):
        self.read_calls.append(
            {
                "device_id": device_id,
                "product_model": product_model,
                "user_id": user_id,
                "capabilities": list(capabilities or []),
                "capability_iids": dict(capability_iids or {}),
            }
        )
        properties = {"motion-detection::motion-tag": self.value} if self.supported else {}
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability="motion-detection",
            properties=properties,
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
        capability="motion-detection",
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
            if prop.get("prop") == "motion-detection::motion-tag":
                self.value = bool(prop.get("value"))
        return {"status": "ok", "response": {"code": 1}}


class FakeNative:
    def __init__(self, *, user_id="u1", identity=("AA:BB:CC:DD:EE:FF", "WYZE_CA")):
        self.user_id = user_id
        self._identity = identity

    def resolve_device_identity(self, camera_id):
        return self._identity


def _adapter(iot, **kwargs) -> WyzeMotionTagAdapter:
    kwargs.setdefault("device_resolver", lambda _cid: ("AA:BB:CC:DD:EE:FF", "WYZE_CA"))
    kwargs.setdefault("model_allowlist", {"WYZE_CA", "HL_CAM4"})
    return WyzeMotionTagAdapter(iot_cloud=iot, **kwargs)


def _build_app(tmp_path, adapter) -> tuple[LookoutConfig, Path, TestClient]:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_wyze_camera()],
    )
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    app.state.ptz_service = PtzControlService(
        cameras=config.cameras,
        motion_tag_adapter=adapter,
        config=config,
        config_path=config_path,
    )
    return config, config_path, TestClient(app)


def test_motion_tag_models_config_wires_allowlist(tmp_path):
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        wyze=WyzeConfig(
            auth_token="test-token-not-real",
            motion_tag_models=["HL_CAM4", "WYZE_ASCENT"],
        ),
        cameras=[_wyze_camera()],
    )
    app = create_app(config=config, config_path=tmp_path / "config.yaml")
    adapter = app.state.ptz_service.motion_tag_adapter
    assert adapter.model_allowlist == {"HL_CAM4", "WYZE_ASCENT"}


def test_capability_map_known_without_motion_detection_is_unsupported():
    iot = FakeIotCloud(supported=True, value=True, capability_iids={}, map_known=True)
    adapter = _adapter(iot)

    state = _run(adapter.get_motion_tag("cam1"))
    assert state.supported is False
    assert state.enabled is None

    written = _run(adapter.set_motion_tag("cam1", True))
    assert written.supported is False
    assert iot.write_bodies == []


class _ProtocolFailingAdapter:
    async def get_motion_tag(self, camera_id):
        raise WyzeProtocolError("Wyze request failed: HTTP 400")

    async def set_motion_tag(self, camera_id, enabled):
        raise WyzeProtocolError("Wyze request failed: HTTP 400")


def test_route_returns_502_on_capable_protocol_error(tmp_path):
    config, _config_path, client = _build_app(tmp_path, _ProtocolFailingAdapter())
    before = config.cameras[0].motion_tag_enabled

    response = client.get("/api/cameras/cam1/motion_tag")
    assert response.status_code == 502
    assert config.cameras[0].motion_tag_enabled == before


# ----------------------------------------------------------------------
# Adapter behavior
# ----------------------------------------------------------------------
def test_motion_tag_adapter_returns_typed_bool_for_capable_device():
    iot = FakeIotCloud(supported=True, value=True)
    state = _run(_adapter(iot).get_motion_tag("cam1"))
    assert state.supported is True
    assert state.enabled is True


def test_motion_tag_adapter_returns_unsupported_for_non_capable_device():
    iot = FakeIotCloud(supported=False)
    state = _run(_adapter(iot).get_motion_tag("cam1"))
    assert state.supported is False
    assert state.enabled is None


def test_non_wyze_camera_is_unsupported_without_calling_cloud():
    iot = FakeIotCloud()
    camera = CameraConfig(id="cam1", name="Cam 1", stream_url="rtsp://localhost/cam1")
    adapter = WyzeMotionTagAdapter(iot_cloud=iot, cameras=[camera])
    state = _run(adapter.get_motion_tag("cam1"))
    assert state.supported is False
    assert iot.read_calls == []


def test_capability_probe_is_cached_per_device():
    iot = FakeIotCloud()
    adapter = _adapter(iot)
    _run(adapter.get_motion_tag("cam1"))
    _run(adapter.get_motion_tag("cam1"))
    assert len(iot.read_calls) == 1


def test_motion_tag_write_forwards_cached_capability_iid():
    iot = FakeIotCloud(supported=True, value=False, iid=7)
    adapter = _adapter(iot)
    result = _run(adapter.set_motion_tag("cam1", True))
    assert result.supported is True
    assert iot.write_bodies[0]["capabilities"][0]["iid"] == 7


def test_model_allowlist_blocks_non_matching_device():
    iot = FakeIotCloud()
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "WYZE_BC"),
        model_allowlist={"WYZE_CA"},
    )
    state = _run(adapter.get_motion_tag("cam1"))
    assert state.supported is False
    assert iot.read_calls == []


@pytest.mark.parametrize("model", ["WYZECP1_JEF", "WYZE_CAKP2JFUS", "HL_PAN3"])
def test_non_capable_models_return_unsupported_without_cloud_call(model):
    iot = FakeIotCloud()
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", model),
    )

    state = _run(adapter.get_motion_tag("cam1"))
    assert state.supported is False
    assert state.enabled is None

    written = _run(adapter.set_motion_tag("cam1", True))
    assert written.supported is False
    assert written.enabled is None
    assert written.raw == {"reason": "unsupported_model"}

    assert iot.read_calls == []
    assert iot.write_bodies == []


def test_default_allowlist_includes_cam_v4():
    assert "HL_CAM4" in DEFAULT_CAPABLE_MOTION_TAG_MODELS

    capable = FakeIotCloud(supported=True, value=True)
    state = _run(
        WyzeMotionTagAdapter(
            iot_cloud=capable,
            device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_CAM4"),
        ).get_motion_tag("cam1")
    )
    assert state.supported is True
    assert len(capable.read_calls) == 1

    non_capable = FakeIotCloud()
    blocked = _run(
        WyzeMotionTagAdapter(
            iot_cloud=non_capable,
            device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "WYZE_CAKP2JFUS"),
        ).get_motion_tag("cam1")
    )
    assert blocked.supported is False
    assert non_capable.read_calls == []


def test_is_token_expired_does_not_treat_1001_as_expiry():
    iot = WyzeIotCloudAdapter()
    body = {"code": 1001, "message": "Failed to read HTTP message"}
    assert iot._is_token_expired(400, body) is False
    assert iot._is_token_expired(400, {"code": 2001}) is True


def test_motion_tag_adapter_implements_port():
    adapter = _adapter(FakeIotCloud())
    assert isinstance(adapter, MotionTagPort) is True


def test_motion_tag_set_then_get_returns_new_value():
    iot = FakeIotCloud(supported=True, value=False)
    adapter = _adapter(iot)
    before = _run(adapter.get_motion_tag("cam1"))
    assert before.supported is True
    assert before.enabled is False
    _run(adapter.set_motion_tag("cam1", True))
    adapter.clear_cache()
    after = _run(adapter.get_motion_tag("cam1"))
    assert after.supported is True
    assert after.enabled is True


def test_motion_tag_adapter_passes_native_user_id():
    iot = FakeIotCloud()
    native = FakeNative(user_id="u1")
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        cameras=[_wyze_camera()],
        native_adapter=native,
        model_allowlist={"WYZE_CA"},
    )
    _run(adapter.get_motion_tag("cam1"))
    assert iot.read_calls[0]["user_id"] == "u1"


# ----------------------------------------------------------------------
# Golden envelope (signer mocked, literal test token only)
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


class _RefreshSpy:
    def __init__(self):
        self.calls = 0

    async def _refresh_wyze_auth(self):
        self.calls += 1
        return True


def test_400_1001_from_capable_model_surfaces_error_without_refresh():
    cfg = WyzeConfig(auth_token="test-token", device_management_base_url="https://example.test")
    client = _MockClient([_Resp(400, {"code": 1001, "message": "Failed to read HTTP message"})])
    spy = _RefreshSpy()
    iot = WyzeIotCloudAdapter(
        config=cfg,
        http_client=client,
        signer=WyzeSigner(access_token="test-token"),
        native_adapter=spy,
        user_id="u1",
    )
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_CAM4"),
    )
    with pytest.raises(WyzeProtocolError) as excinfo:
        _run(adapter.get_motion_tag("cam1"))
    assert excinfo.value.status_code == 502
    assert "HTTP 400" in str(excinfo.value)
    assert spy.calls == 0
    assert len(client.calls) == 1


def test_motion_tag_write_envelope_uses_signer_and_no_real_tokens():
    cfg = WyzeConfig(auth_token="test-token", device_management_base_url="https://example.test")
    client = _MockClient(
        [
            _Resp(
                200,
                {
                    "code": 1,
                    "data": {
                        "capabilities": [
                            {
                                "name": "motion-detection",
                                "iid": 7,
                                "properties": [
                                    {"prop": "motion-detection::motion-tag", "value": False}
                                ],
                            }
                        ]
                    },
                },
            ),
            _Resp(200, {"code": 1}),
        ]
    )
    iot = WyzeIotCloudAdapter(
        config=cfg,
        http_client=client,
        signer=WyzeSigner(access_token="test-token"),
        user_id="u1",
    )
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "WYZE_CA"),
        user_id="u1",
        model_allowlist={"WYZE_CA"},
    )
    result = _run(adapter.set_motion_tag("cam1", True))
    assert result.supported is True
    body = json.loads(client.calls[1]["content"])
    assert body["capabilities"][0]["properties"][0] == {
        "separator": "::",
        "prop": "motion-detection::motion-tag",
        "value": True,
    }
    assert "signature2" in client.calls[1]["headers"]
    assert "test-token" not in str(body)


# ----------------------------------------------------------------------
# Domain service + REST routes
# ----------------------------------------------------------------------
def test_motion_tag_read_returns_200_and_post_returns_501_when_unsupported(tmp_path):
    iot = FakeIotCloud(supported=False)
    adapter = _adapter(iot)
    _config, _config_path, client = _build_app(tmp_path, adapter)

    get_resp = client.get("/api/cameras/cam1/motion_tag")
    assert get_resp.status_code == 200
    assert get_resp.json() == {"supported": False, "enabled": None}

    before = _config.cameras[0].motion_tag_enabled
    post_resp = client.post("/api/cameras/cam1/motion_tag", json={"enabled": True})
    assert post_resp.status_code == 501
    assert _config.cameras[0].motion_tag_enabled == before


def test_route_rejects_non_capable_model_without_cloud_call(tmp_path):
    iot = FakeIotCloud()
    adapter = WyzeMotionTagAdapter(
        iot_cloud=iot,
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "WYZE_CAKP2JFUS"),
    )
    config, _config_path, client = _build_app(tmp_path, adapter)

    read = client.get("/api/cameras/cam1/motion_tag")
    assert read.status_code == 200
    assert read.json() == {"supported": False, "enabled": None}

    before = config.cameras[0].motion_tag_enabled
    written = client.post("/api/cameras/cam1/motion_tag", json={"enabled": True})
    assert written.status_code == 501
    assert config.cameras[0].motion_tag_enabled == before

    assert iot.read_calls == []
    assert iot.write_bodies == []


def test_set_motion_tag_round_trips_and_persists_config(tmp_path):
    iot = FakeIotCloud(supported=True, value=False)
    adapter = _adapter(iot)
    config, config_path, client = _build_app(tmp_path, adapter)

    read = client.get("/api/cameras/cam1/motion_tag")
    assert read.status_code == 200
    assert read.json() == {"supported": True, "enabled": False}

    written = client.post("/api/cameras/cam1/motion_tag", json={"enabled": True})
    assert written.status_code == 200
    assert written.json()["supported"] is True
    assert written.json()["enabled"] is True
    assert config.cameras[0].motion_tag_enabled is True

    persisted = yaml.safe_load(config_path.read_text())["cameras"][0]["motion_tag_enabled"]
    assert persisted is True

    after = client.get("/api/cameras/cam1/motion_tag")
    assert after.status_code == 200
    assert after.json() == {"supported": True, "enabled": True}


async def test_set_motion_tag_audits_success():
    audit_logger = AuditLogger(db_path=":memory:")
    adapter = _adapter(FakeIotCloud(supported=True, value=False))
    service = PtzControlService(
        cameras=[_wyze_camera()],
        motion_tag_adapter=adapter,
        audit_logger=audit_logger,
    )
    result = await service.set_motion_tag("cam1", True)
    assert result.supported is True
    entries = await audit_logger.query()
    assert entries[0]["action"] == "motion_tag_set"


async def test_set_motion_tag_skips_cloud_write_when_unsupported():
    iot = FakeIotCloud(supported=False)
    adapter = _adapter(iot)
    camera = _wyze_camera()
    service = PtzControlService(cameras=[camera], motion_tag_adapter=adapter)
    with pytest.raises(Exception) as excinfo:
        await service.set_motion_tag("cam1", True)
    assert getattr(excinfo.value, "status_code", None) == 501
    assert iot.write_bodies == []
    assert camera.motion_tag_enabled is None


# ----------------------------------------------------------------------
# Architecture
# ----------------------------------------------------------------------
def test_motion_tag_adapter_is_not_imported_by_domain():
    domain_dir = Path("src/lookout/domain")
    imported_modules: list[str] = []
    for py_file in domain_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
    assert "lookout.adapters.wyze_iot_cloud" not in imported_modules
