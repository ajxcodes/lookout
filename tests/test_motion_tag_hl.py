"""Tests for the HL/Hualai motion-tag lane (#111)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.web.app import create_app
from lookout.adapters.wyze_device_identity import (
    WyzeDeviceIdentity,
    resolve_wyze_device_identity,
)
from lookout.adapters.wyze_hl import (
    DEFAULT_HL_MOTION_TAG_MODELS,
    HL_MOTION_TAG_PROPERTY,
    HlControlError,
    HlControlUnsupported,
    HualaiBridgePropertyTransport,
    WyzeHlMotionTagAdapter,
)
from lookout.adapters.wyze_motion_tag_router import WyzeMotionTagRouter
from lookout.config.schema import CameraConfig, LookoutConfig, StorageConfig, WyzeConfig
from lookout.domain.models import MotionTagState, MotionTagWriteResult
from lookout.domain.ptz_service import PtzControlService
from lookout.ports.egress import HlCameraPropertyPort, MotionTagPort


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _pan_camera(**overrides: Any) -> CameraConfig:
    data = {
        "id": "mochi_pan_cam",
        "name": "Mochi Pan",
        "stream_url": "wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_PAN3&subtype=hd",
        "native_provider": "wyze",
        "native_device_id": "AA:BB:CC:DD:EE:FF",
    }
    data.update(overrides)
    return CameraConfig(**data)


class RecordingTransport:
    """Records property reads/writes and serves a configurable value."""

    def __init__(self, value: Any = 1) -> None:
        self.value = value
        self.calls: list[tuple[Any, ...]] = []
        self.writes: list[tuple[str, str, Any]] = []

    async def get_property(self, camera_name: str, property_name: str) -> Any:
        self.calls.append(("get", camera_name, property_name))
        return {property_name: self.value}

    async def set_property(self, camera_name: str, property_name: str, value: Any) -> Any:
        self.calls.append(("set", camera_name, property_name, value))
        self.writes.append((camera_name, property_name, value))
        self.value = value
        return {property_name: value}


class RaisingTransport:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def get_property(self, camera_name: str, property_name: str) -> Any:
        raise self.exc

    async def set_property(self, camera_name: str, property_name: str, value: Any) -> Any:
        raise self.exc


# ----------------------------------------------------------------------
# Adapter encoding and gating
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw", [1, "1", "on", "true", True])
def test_hl_adapter_decodes_on_encodings(raw: Any) -> None:
    transport = RecordingTransport(value=raw)
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(
        supported=True, enabled=True
    )


@pytest.mark.parametrize("raw", [2, "2", "off", "false", False])
def test_hl_adapter_decodes_off_encodings(raw: Any) -> None:
    transport = RecordingTransport(value=raw)
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(
        supported=True, enabled=False
    )


@pytest.mark.parametrize("raw", [None, "unknown"])
def test_hl_adapter_decodes_unreadable_values_as_unsupported(raw: Any) -> None:
    transport = RecordingTransport(value=raw)
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(
        supported=False, enabled=None
    )


def test_hl_adapter_decodes_on_off_encodings() -> None:
    """TR-5: every documented on/off encoding decodes, unreadable values do not."""
    for raw in (1, "1", "on", "true", True):
        adapter = WyzeHlMotionTagAdapter(
            transport=RecordingTransport(value=raw), cameras=[_pan_camera()]
        )
        assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(True, True)
    for raw in (2, "2", "off", "false", False):
        adapter = WyzeHlMotionTagAdapter(
            transport=RecordingTransport(value=raw), cameras=[_pan_camera()]
        )
        assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(True, False)
    for raw in (None, "unknown"):
        adapter = WyzeHlMotionTagAdapter(
            transport=RecordingTransport(value=raw), cameras=[_pan_camera()]
        )
        assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(False, None)


def test_hl_adapter_encodes_true_as_1_and_false_as_2() -> None:
    transport = RecordingTransport(value=2)
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])

    first = _run(adapter.set_motion_tag("mochi_pan_cam", True))
    second = _run(adapter.set_motion_tag("mochi_pan_cam", False))

    assert first.supported is True
    assert second.supported is True
    assert transport.writes == [
        ("mochi_pan_cam", "motion_tagging", 1),
        ("mochi_pan_cam", "motion_tagging", 2),
    ]


@pytest.mark.parametrize("model", ["HL_CAM4", "WYZECP1_JEF"])
def test_hl_adapter_gates_unlisted_models_without_transport_calls(model: str) -> None:
    transport = RecordingTransport()
    camera = _pan_camera(native_model=model)
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[camera])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(False, None)
    assert _run(adapter.set_motion_tag("mochi_pan_cam", True)) == MotionTagWriteResult(
        supported=False, enabled=None, raw={}
    )
    assert transport.calls == []


def test_hl_adapter_without_transport_is_unsupported() -> None:
    adapter = WyzeHlMotionTagAdapter(transport=None, cameras=[_pan_camera()])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(False, None)
    assert _run(adapter.set_motion_tag("mochi_pan_cam", True)) == MotionTagWriteResult(
        supported=False, enabled=None, raw={}
    )


def test_hl_adapter_maps_unsupported_bridge_error_to_unsupported() -> None:
    transport = RaisingTransport(HlControlUnsupported("HTTP 404"))
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])

    assert _run(adapter.get_motion_tag("mochi_pan_cam")) == MotionTagState(False, None)
    assert _run(adapter.set_motion_tag("mochi_pan_cam", True)) == MotionTagWriteResult(
        supported=False, enabled=None, raw={}
    )


def test_hl_adapter_surfaces_transport_failure_as_502() -> None:
    transport = RaisingTransport(HlControlError("bridge timeout"))
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[_pan_camera()])
    router = WyzeMotionTagRouter(cloud=None, hl=adapter)

    with pytest.raises(HlControlError) as excinfo:
        _run(router.get_motion_tag("mochi_pan_cam"))
    assert excinfo.value.status_code == 502

    with pytest.raises(HlControlError) as excinfo:
        _run(router.set_motion_tag("mochi_pan_cam", True))
    assert excinfo.value.status_code == 502


def test_hl_adapter_uses_ptz_cam_name_for_bridge_topic() -> None:
    transport = RecordingTransport()
    camera = _pan_camera(ptz_cam_name="mochi-pan")
    adapter = WyzeHlMotionTagAdapter(transport=transport, cameras=[camera])

    _run(adapter.get_motion_tag("mochi_pan_cam"))

    assert transport.calls == [("get", "mochi-pan", "motion_tagging")]
    assert ("get", "mochi_pan_cam", "motion_tagging") not in transport.calls


def test_hl_adapter_and_transport_implement_ports() -> None:
    adapter = WyzeHlMotionTagAdapter(transport=RecordingTransport(), cameras=[_pan_camera()])
    transport = HualaiBridgePropertyTransport(base_url="http://bridge.test:5000")

    assert isinstance(adapter, MotionTagPort) is True
    assert isinstance(transport, HlCameraPropertyPort) is True


def test_default_hl_allowlist_covers_cam_v3_and_pan_v3() -> None:
    assert frozenset({"WYZE_CAKP2JFUS", "HL_PAN3"}) == DEFAULT_HL_MOTION_TAG_MODELS
    assert HL_MOTION_TAG_PROPERTY == "motion_tagging"


# ----------------------------------------------------------------------
# Bridge transport
# ----------------------------------------------------------------------
def test_hl_transport_reads_bridge_topic() -> None:
    transport = HualaiBridgePropertyTransport(base_url="http://bridge.test:5000")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("http://bridge.test:5000/api/mochi_pan_cam/motion_tagging").mock(
            return_value=httpx.Response(200, json={"motion_tagging": "1"})
        )
        raw = _run(transport.get_property("mochi_pan_cam", "motion_tagging"))

    assert route.called is True
    assert raw == {"motion_tagging": "1"}


def test_hl_transport_posts_on_off_form_payload() -> None:
    transport = HualaiBridgePropertyTransport(base_url="http://bridge.test:5000")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://bridge.test:5000/api/mochi_pan_cam/motion_tagging").mock(
            return_value=httpx.Response(200, json={"motion_tagging": "1"})
        )
        _run(transport.set_property("mochi_pan_cam", "motion_tagging", 1))
        first = route.calls.last.request
        _run(transport.set_property("mochi_pan_cam", "motion_tagging", 2))
        second = route.calls.last.request

    assert first.content == b"value=1"
    assert second.content == b"value=2"


def test_hl_transport_maps_http_status_to_typed_errors() -> None:
    transport = HualaiBridgePropertyTransport(base_url="http://bridge.test:5000")

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://bridge.test:5000/api/mochi_pan_cam/motion_tagging").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(HlControlUnsupported):
            _run(transport.get_property("mochi_pan_cam", "motion_tagging"))

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://bridge.test:5000/api/mochi_pan_cam/motion_tagging").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(HlControlError) as excinfo:
            _run(transport.get_property("mochi_pan_cam", "motion_tagging"))
        assert excinfo.value.status_code == 502

    with pytest.raises(ValueError):
        HualaiBridgePropertyTransport(base_url="")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").mock(return_value=httpx.Response(200))
        with pytest.raises(ValueError):
            _run(transport.get_property("mochi_pan_cam", "Not-Valid"))
        assert route.called is False


# ----------------------------------------------------------------------
# End-to-end service + routes
# ----------------------------------------------------------------------
class LoopbackBridge:
    """Loopback bridge property transport used by service/route tests."""

    def __init__(self, value: str = "2") -> None:
        self.value = value
        self.calls: list[tuple[Any, ...]] = []
        self.writes: list[tuple[str, str, Any]] = []

    async def get_property(self, camera_name: str, property_name: str) -> Any:
        self.calls.append(("get", camera_name, property_name))
        return {property_name: self.value}

    async def set_property(self, camera_name: str, property_name: str, value: Any) -> Any:
        self.calls.append(("set", camera_name, property_name, value))
        self.writes.append((camera_name, property_name, value))
        self.value = str(value)
        return {property_name: self.value}


def test_hl_route_reads_toggles_persists_and_audits(tmp_path: Path) -> None:
    transport = LoopbackBridge(value="2")
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[_pan_camera(native_model="HL_PAN3")],
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
    audit_logger = AuditLogger(db_path=":memory:")
    service = PtzControlService(
        cameras=config.cameras,
        motion_tag_adapter=WyzeMotionTagRouter(
            hl=WyzeHlMotionTagAdapter(transport=transport, cameras=lambda: config.cameras)
        ),
        audit_logger=audit_logger,
        config=config,
        config_path=config_path,
    )

    before = _run(service.get_motion_tag("mochi_pan_cam"))
    written = _run(service.set_motion_tag("mochi_pan_cam", True))

    assert before == MotionTagState(supported=True, enabled=False)
    assert written == MotionTagWriteResult(
        supported=True,
        enabled=True,
        raw={"transport": "hl", "property": "motion_tagging", "value": 1},
    )
    assert config.cameras[0].motion_tag_enabled is True
    persisted = yaml.safe_load(config_path.read_text())["cameras"][0]["motion_tag_enabled"]
    assert persisted is True
    entries = _run(audit_logger.query())
    assert entries[0]["action"] == "motion_tag_set"
    assert transport.writes == [("mochi_pan_cam", "motion_tagging", 1)]


def _build_app(
    tmp_path: Path, config: LookoutConfig, adapter: Any
) -> tuple[LookoutConfig, Path, TestClient]:
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    app.state.ptz_service = PtzControlService(
        cameras=config.cameras,
        motion_tag_adapter=adapter,
        config=config,
        config_path=config_path,
    )
    return config, config_path, TestClient(app)


def test_hl_route_degrades_for_original_pan(tmp_path: Path) -> None:
    office = CameraConfig(
        id="office_cam",
        name="Office",
        stream_url="wyze://?mac=11:22:33:44:55:66&model=WYZECP1_JEF&subtype=hd",
        native_provider="wyze",
        native_device_id="11:22:33:44:55:66",
        native_model="WYZECP1_JEF",
    )
    transport = LoopbackBridge()
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        cameras=[office],
    )
    adapter = WyzeMotionTagRouter(hl=WyzeHlMotionTagAdapter(transport=transport, cameras=[office]))
    config, _config_path, client = _build_app(tmp_path, config, adapter)

    read = client.get("/api/cameras/office_cam/motion_tag")
    written = client.post("/api/cameras/office_cam/motion_tag", json={"enabled": True})

    assert read.status_code == 200
    assert read.json() == {"supported": False, "enabled": None}
    assert written.status_code == 501
    assert config.cameras[0].motion_tag_enabled is None
    assert transport.calls == []


def test_hl_route_returns_200_false_when_transport_missing(tmp_path: Path) -> None:
    config = LookoutConfig(
        storage=StorageConfig(base_path=str(tmp_path / "media")),
        wyze=WyzeConfig(),
        cameras=[_pan_camera(native_model="HL_PAN3")],
    )
    assert config.wyze.external_bridge_url is None
    config_path = tmp_path / "config.yaml"
    app = create_app(config=config, config_path=config_path)
    client = TestClient(app)

    read = client.get("/api/cameras/mochi_pan_cam/motion_tag")
    written = client.post("/api/cameras/mochi_pan_cam/motion_tag", json={"enabled": True})

    assert read.status_code == 200
    assert read.json() == {"supported": False, "enabled": None}
    assert written.status_code == 501
    assert config.cameras[0].motion_tag_enabled is None


def test_identity_prefers_native_model_over_stream_url() -> None:
    camera = CameraConfig(
        id="mochi_pan_cam",
        name="Mochi Pan",
        stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&subtype=hd",
        native_model="HL_PAN3",
    )
    assert resolve_wyze_device_identity("mochi_pan_cam", cameras=[camera]) == WyzeDeviceIdentity(
        "AA:BB:CC:DD:EE:FF", "HL_PAN3"
    )

    fallback = CameraConfig(
        id="fallback",
        name="Fallback",
        stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_PAN3&subtype=hd",
    )
    assert resolve_wyze_device_identity("fallback", cameras=[fallback]) == WyzeDeviceIdentity(
        "AA:BB:CC:DD:EE:FF", "HL_PAN3"
    )

    non_wyze = CameraConfig(id="rtsp", name="RTSP", stream_url="rtsp://localhost/cam")
    assert resolve_wyze_device_identity("rtsp", cameras=[non_wyze]) is None
