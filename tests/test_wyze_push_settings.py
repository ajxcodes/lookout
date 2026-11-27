"""Unit and integration tests for the Wyze push-notification settings sync (#85)."""

import ast
import asyncio
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.adapters.wyze_iot_cloud import (
    IotPropertyResult,
    WyzeIotCloudAdapter,
    WyzeProtocolError,
)
from lookout.adapters.wyze_push_settings import WyzePushSettingsAdapter
from lookout.adapters.wyze_signing import WyzeSigner
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig, WyzeConfig
from lookout.domain.models import (
    PUSH_SETTING_KEYS,
    PushSettingsWriteResult,
)
from lookout.domain.ptz_service import PtzControlService
from lookout.ports.egress import WyzePushSettingsPort


def _run(coro):
    return asyncio.run(coro)


def _wyze_camera(**overrides) -> CameraConfig:
    data = {
        "id": "cam1",
        "name": "Cam 1",
        "stream_url": "wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_CAM4&subtype=hd",
        "native_provider": "wyze",
        "native_device_id": "AA:BB:CC:DD:EE:FF",
    }
    data.update(overrides)
    return CameraConfig(**data)


_FULL_SETTINGS = {
    "MotionPush": True,
    "SoundPush": False,
    "SmartDetectionPush": True,
    "LowBatteryPush": False,
    "AiPush": True,
    "PushSwitch": True,
}

_FULL_IIDS = {
    "MotionPush": 11,
    "SoundPush": 12,
    "SmartDetectionPush": 13,
    "LowBatteryPush": 14,
    "AiPush": 15,
    "PushSwitch": 16,
}


def _payload(settings: dict[str, bool]) -> dict[str, dict[str, object]]:
    """Build a per-capability ``{capability: {leaf: value}}`` read payload."""
    return {capability: {"push": value} for capability, value in settings.items()}


class FakeIotCloud:
    """Records envelopes and serves a configurable per-capability response."""

    def __init__(
        self,
        *,
        payload: dict[str, dict[str, object]] | None = None,
        capability_iids: dict[str, int] | None = None,
        map_known: bool = True,
        write_error: Exception | None = None,
    ):
        self.payload = dict(payload or {})
        self.capability_iids = (
            dict(capability_iids)
            if capability_iids is not None
            else {capability: 11 + index for index, capability in enumerate(self.payload)}
        )
        self.map_known = map_known
        self.write_error = write_error
        self.read_calls: list[dict] = []
        self.write_bodies: list[dict] = []

    async def get_iot_prop(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capabilities=None,
        properties=None,
        capability_iids=None,
        capability_properties=None,
    ):
        self.read_calls.append(
            {
                "device_id": device_id,
                "product_model": product_model,
                "user_id": user_id,
                "capabilities": list(capabilities or []),
                "properties": list(properties or []),
                "capability_iids": dict(capability_iids or {}),
                "capability_properties": dict(capability_properties or {}),
            }
        )
        flat: dict[str, object] = {}
        for capability, leaves in self.payload.items():
            for leaf, value in leaves.items():
                flat[f"{capability}::{leaf}"] = value
        return IotPropertyResult(
            device_id=device_id,
            product_model=product_model,
            capability="MotionPush",
            properties=flat,
            raw={"code": 1},
            capability_iid=self.capability_iids.get("MotionPush"),
            capability_iids=dict(self.capability_iids),
            capability_map_known=self.map_known,
            properties_by_capability={cap: dict(leaves) for cap, leaves in self.payload.items()},
        )

    async def run_action(
        self,
        device_id,
        product_model,
        *,
        user_id=None,
        capability="MotionPush",
        properties=None,
        capability_iid=None,
    ):
        if self.write_error is not None:
            raise self.write_error
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
        return {"status": "ok", "response": {"code": 1}}


def _adapter(iot, **kwargs) -> WyzePushSettingsAdapter:
    kwargs.setdefault("device_resolver", lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_CAM4"))
    return WyzePushSettingsAdapter(iot_cloud=iot, **kwargs)


def _build_app(tmp_path, adapter, *, camera=None) -> tuple[LookoutConfig, Path, TestClient]:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[camera or _wyze_camera()],
    )
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    app.state.ptz_service = PtzControlService(
        cameras=config.cameras,
        push_settings_adapter=adapter,
        config=config,
        config_path=config_path,
    )
    return config, config_path, TestClient(app)


# ----------------------------------------------------------------------
# TR-7: tri-state config
# ----------------------------------------------------------------------
def test_push_settings_config_is_tri_state_and_round_trips():
    default = CameraConfig(id="cam1", name="Cam 1", stream_url="rtsp://localhost/cam1")
    assert default.wyze_push_settings is None

    enabled = CameraConfig(
        id="cam1",
        name="Cam 1",
        stream_url="rtsp://localhost/cam1",
        wyze_push_settings={"MotionPush": True},
    )
    assert "wyze_push_settings" in enabled.model_dump()
    assert enabled.wyze_push_settings == {"MotionPush": True}


# ----------------------------------------------------------------------
# TR-8: one read returns every advertised capability
# ----------------------------------------------------------------------
def test_push_settings_adapter_reads_all_six_capabilities_in_one_call():
    iot = FakeIotCloud(payload=_payload(_FULL_SETTINGS), capability_iids=_FULL_IIDS)
    state = _run(_adapter(iot).get_push_settings("cam1"))

    assert state.settings == {
        "MotionPush": True,
        "SoundPush": False,
        "SmartDetectionPush": True,
        "LowBatteryPush": False,
        "AiPush": True,
        "PushSwitch": True,
    }
    assert len(iot.read_calls) == 1
    assert iot.read_calls[0]["capabilities"] == list(PUSH_SETTING_KEYS)


# ----------------------------------------------------------------------
# TR-11/TR-12: golden envelopes (mocked transport, literal test token only)
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


def _golden_read_response() -> _Resp:
    return _Resp(
        200,
        {
            "code": 1,
            "data": {
                "capabilities": [
                    {
                        "name": capability,
                        "iid": 11 + index,
                        "properties": [{"prop": f"{capability}::push", "value": value}],
                    }
                    for index, (capability, value) in enumerate(_FULL_SETTINGS.items())
                ]
            },
        },
    )


def test_push_settings_write_envelope_targets_single_capability():
    client = _MockClient([_golden_read_response(), _Resp(200, {"code": 1})])
    adapter = WyzePushSettingsAdapter(
        iot_cloud=_golden_iot(client),
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_CAM4"),
        user_id="u1",
    )
    result = _run(adapter.set_push_setting("cam1", "MotionPush", True))
    assert result.supported is True and result.enabled is True

    write_body = json.loads(client.calls[1]["content"])
    assert len(write_body["capabilities"]) == 1
    assert write_body["capabilities"][0]["properties"] == [
        {"separator": "::", "prop": "MotionPush::push", "value": True}
    ]
    assert write_body["capabilities"][0]["iid"] == 11


def test_push_settings_read_envelope_never_carries_access_token():
    client = _MockClient([_golden_read_response()])
    adapter = WyzePushSettingsAdapter(
        iot_cloud=_golden_iot(client),
        device_resolver=lambda _cid: ("AA:BB:CC:DD:EE:FF", "HL_CAM4"),
        user_id="u1",
    )
    _run(adapter.get_push_settings("cam1"))

    assert "signature2" in client.calls[0]["headers"]
    assert "test-token" not in client.calls[0]["content"].decode()


# ----------------------------------------------------------------------
# TR-13/TR-14: POST persists and merges
# ----------------------------------------------------------------------
def test_push_settings_post_toggles_one_key_and_persists_config(tmp_path):
    iot = FakeIotCloud(payload=_payload({"MotionPush": False, "SoundPush": True}))
    config, config_path, client = _build_app(tmp_path, _adapter(iot))

    written = client.post(
        "/api/cameras/cam1/wyze_push_settings",
        json={"key": "MotionPush", "enabled": True},
    )
    assert written.status_code == 200
    assert config.cameras[0].wyze_push_settings == {"MotionPush": True}
    persisted = yaml.safe_load(config_path.read_text())["cameras"][0]
    assert persisted["wyze_push_settings"]["MotionPush"] is True
    assert len(iot.write_bodies) == 1


def test_push_settings_write_merges_into_existing_config_map(tmp_path):
    camera = _wyze_camera(wyze_push_settings={"SoundPush": False})
    iot = FakeIotCloud(payload=_payload({"MotionPush": False, "SoundPush": False}))
    config, _config_path, client = _build_app(tmp_path, _adapter(iot), camera=camera)

    written = client.post(
        "/api/cameras/cam1/wyze_push_settings",
        json={"key": "MotionPush", "enabled": True},
    )
    assert written.status_code == 200
    assert config.cameras[0].wyze_push_settings == {"SoundPush": False, "MotionPush": True}


# ----------------------------------------------------------------------
# TR-15: audit
# ----------------------------------------------------------------------
async def test_push_settings_set_audits_success():
    audit_logger = AuditLogger(db_path=":memory:")
    adapter = _adapter(FakeIotCloud(payload=_payload({"MotionPush": False})))
    service = PtzControlService(
        cameras=[_wyze_camera()],
        push_settings_adapter=adapter,
        audit_logger=audit_logger,
    )
    result = await service.set_push_setting("cam1", "MotionPush", True)
    assert result.supported is True
    entries = await audit_logger.query()
    assert entries[0]["action"] == "wyze_push_settings_set"


# ----------------------------------------------------------------------
# TR-16/TR-17/TR-18: unsupported degradation
# ----------------------------------------------------------------------
def test_push_settings_read_marks_absent_capability_unsupported():
    iot = FakeIotCloud(
        payload=_payload({"SoundPush": False}),
        capability_iids={"SoundPush": 12},
        map_known=True,
    )
    state = _run(_adapter(iot).get_push_settings("cam1"))

    assert "MotionPush" in state.unsupported
    assert "MotionPush" not in state.settings


def test_push_settings_post_unsupported_key_returns_501_without_mutation(tmp_path):
    iot = FakeIotCloud(
        payload=_payload({"SoundPush": False}),
        capability_iids={"SoundPush": 12},
        map_known=True,
    )
    config, _config_path, client = _build_app(tmp_path, _adapter(iot))

    response = client.post(
        "/api/cameras/cam1/wyze_push_settings",
        json={"key": "MotionPush", "enabled": True},
    )
    assert response.status_code == 501
    assert iot.write_bodies == []
    assert config.cameras[0].wyze_push_settings is None


def test_push_settings_read_reports_fully_unsupported_device(tmp_path):
    iot = FakeIotCloud(payload={}, capability_iids={}, map_known=False)
    adapter = _adapter(iot, device_resolver=lambda _cid: None)
    _config, _config_path, client = _build_app(tmp_path, adapter)

    payload = client.get("/api/cameras/cam1/wyze_push_settings").json()
    assert payload == {
        "supported": False,
        "settings": {},
        "unsupported": list(PUSH_SETTING_KEYS),
    }


# ----------------------------------------------------------------------
# TR-19/TR-20: request validation and truthful protocol failure
# ----------------------------------------------------------------------
def test_push_settings_post_unknown_key_returns_400(tmp_path):
    iot = FakeIotCloud(payload=_payload({"MotionPush": False}))
    _config, _config_path, client = _build_app(tmp_path, _adapter(iot))

    response = client.post(
        "/api/cameras/cam1/wyze_push_settings",
        json={"key": "PushNotificationAction", "enabled": True},
    )
    assert response.status_code == 400
    assert iot.write_bodies == []


def test_push_settings_post_protocol_error_returns_502_without_mutation(tmp_path):
    iot = FakeIotCloud(
        payload=_payload({"MotionPush": False}),
        write_error=WyzeProtocolError("Wyze run_action failed: code=1001 msg=Invalid"),
    )
    config, _config_path, client = _build_app(tmp_path, _adapter(iot))

    response = client.post(
        "/api/cameras/cam1/wyze_push_settings",
        json={"key": "MotionPush", "enabled": True},
    )
    assert response.status_code == 502
    assert config.cameras[0].wyze_push_settings is None


# ----------------------------------------------------------------------
# TR-23: port conformance
# ----------------------------------------------------------------------
def test_push_settings_adapter_implements_port():
    adapter = _adapter(FakeIotCloud(payload=_payload({"MotionPush": False})))
    assert isinstance(adapter, WyzePushSettingsPort) is True


# ----------------------------------------------------------------------
# TR-28: architecture
# ----------------------------------------------------------------------
def test_push_settings_adapter_is_not_imported_by_domain():
    domain_dir = Path("src/lookout/domain")
    imported_modules: list[str] = []
    for py_file in domain_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
    assert "lookout.adapters.wyze_push_settings" not in imported_modules


# ----------------------------------------------------------------------
# TR-29/TR-30: full iid map and observed property leaf
# ----------------------------------------------------------------------
def test_push_settings_reprobe_forwards_full_capability_iid_map():
    iot = FakeIotCloud(payload=_payload(_FULL_SETTINGS), capability_iids=_FULL_IIDS)
    adapter = _adapter(iot)
    _run(adapter.get_push_settings("cam1"))
    adapter._states.clear()
    _run(adapter.get_push_settings("cam1"))

    assert iot.read_calls[1]["capability_iids"] == {
        "MotionPush": 11,
        "SoundPush": 12,
        "SmartDetectionPush": 13,
        "LowBatteryPush": 14,
        "AiPush": 15,
        "PushSwitch": 16,
    }


def test_push_settings_adapter_uses_observed_property_leaf():
    iot = FakeIotCloud(
        payload={"MotionPush": {"enabled": True}},
        capability_iids={"MotionPush": 11},
    )
    adapter = _adapter(iot)
    state = _run(adapter.get_push_settings("cam1"))
    assert state.settings == {"MotionPush": True}

    result = _run(adapter.set_push_setting("cam1", "MotionPush", False))
    assert result.supported is True
    assert iot.write_bodies[0]["capabilities"][0]["properties"][0]["prop"] == "MotionPush::enabled"


# ----------------------------------------------------------------------
# Supporting sanity: unsupported write result shape
# ----------------------------------------------------------------------
def test_push_settings_write_result_for_unsupported_key_is_explicit():
    iot = FakeIotCloud(payload=_payload({"MotionPush": True}))
    adapter = _adapter(iot)
    _run(adapter.get_push_settings("cam1"))
    result = _run(adapter.set_push_setting("cam1", "SoundPush", True))
    assert result == PushSettingsWriteResult(supported=False, key="SoundPush", enabled=None, raw={})
    assert iot.write_bodies == []
    state = _run(adapter.get_push_settings("cam1"))
    assert state.settings == {"MotionPush": True}
    assert "SoundPush" in state.unsupported
