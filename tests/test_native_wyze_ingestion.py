"""Real-code-path tests for native Wyze ingestion and PTZ."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from lookout.adapters.native_stream_manager import MediaCaptureError
from lookout.adapters.wyze_native import (
    WyzeApiError,
    WyzeNativeAdapter,
    WyzeStreamSourceError,
    WyzeTutkPtzAdapter,
)
from lookout.adapters.wyze_native import (
    _as_utc as _wyze_as_utc,
)
from lookout.config.schema import CameraConfig, WyzeConfig
from lookout.ports.egress import PtzControlPort, VideoRecorderPort

JPEG = b"\xff\xd8\xff\xe0" + b"wyze-jpeg-payload" * 64
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08mdat" + b"\x00" * 256

WYZE_DEVICE = {
    "id": "A1B2C3D4E5F6",
    "name": "Office Cam",
    "mac": "A1B2C3D4E5F6",
    "model": "WYZECP1_JEF",
    "ip": "192.168.1.50",
    "p2p_id": "UPE2UNBNWDFAKE01AB",
    "enr": "mnfNV14qMEFPrcnS/abc",
    "native_provider": "wyze",
}


class FakeStreamManager:
    def __init__(
        self,
        frame_bytes: bytes = JPEG,
        clip_bytes: bytes = MP4,
        frame_error: Exception | None = None,
        clip_error: Exception | None = None,
    ) -> None:
        self.frame_bytes = frame_bytes
        self.clip_bytes = clip_bytes
        self.frame_error = frame_error
        self.clip_error = clip_error
        self.ensured: list[tuple[str, str | None]] = []

    async def ensure_stream(self, camera_id: str, source: str | None = None) -> None:
        self.ensured.append((camera_id, source))

    async def frame(self, camera_id: str) -> bytes:
        if self.frame_error is not None:
            raise self.frame_error
        return self.frame_bytes

    async def clip(self, camera_id: str, duration_seconds: float) -> bytes:
        if self.clip_error is not None:
            raise self.clip_error
        return self.clip_bytes


def _camera(camera_id: str = "office_cam") -> CameraConfig:
    return CameraConfig(
        id=camera_id,
        name="Office Cam",
        stream_url=(
            "wyze://192.168.1.50?uid=UPE2UNBNWDFAKE01AB"
            "&enr=mnfNV14qMEFPrcnS%2Fabc&mac=A1B2C3D4E5F6"
            "&model=WYZECP1_JEF&subtype=hd&dtls=true"
        ),
        native_provider="wyze",
        native_device_id="A1B2C3D4E5F6",
        ptz_enabled=True,
        ptz_protocol="native-wyze",
    )


class FakePtzClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.response = response or {"status": "ok", "action_key": "rotary_degree"}

    async def run_action(
        self, camera_id: str, action_key: str, action_params: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((camera_id, action_key, action_params))
        return self.response


class FakeApiResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class FakeApiClient:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    def run_action(self, **kwargs: Any) -> FakeApiResponse:
        self.calls.append(kwargs)
        return FakeApiResponse(self.data)


class FakeWyzeRawClient:
    def __init__(
        self,
        cameras: list[dict[str, Any]] | None = None,
        action_data: dict[str, Any] | None = None,
        events: list[Any] | None = None,
    ) -> None:
        self.raw_cameras = cameras or []
        self._event_items = events or []
        self.api = FakeApiClient(action_data or {"code": "1", "msg": "SUCCESS", "data": {}})
        self.cameras = SimpleNamespace(
            _list_cameras=lambda: list(self.raw_cameras),
            _api_client=lambda: self.api,
        )
        self.events = SimpleNamespace(list=self._list_events)

    def _list_events(self, **kwargs: Any) -> list[Any]:
        return list(self._event_items)


def test_wyze_adapter_implements_ports() -> None:
    adapter = WyzeNativeAdapter(config=WyzeConfig())
    assert isinstance(adapter, VideoRecorderPort)
    assert isinstance(adapter, PtzControlPort)
    assert isinstance(WyzeTutkPtzAdapter(config=WyzeConfig()), PtzControlPort)


def test_build_stream_source_uses_validated_wyze_url() -> None:
    adapter = WyzeNativeAdapter(config=WyzeConfig())
    source = adapter.build_stream_source(
        {
            "ip": "192.168.1.50",
            "p2p_id": "UPE2UNBNWDFAKE01AB",
            "enr": "mnfNV14qMEFPrcnS/abc",
            "mac": "A1B2C3D4E5F6",
            "model": "WYZECP1_JEF",
        }
    )
    assert source == (
        "wyze://192.168.1.50"
        f"?uid={quote('UPE2UNBNWDFAKE01AB', safe='')}"
        f"&enr={quote('mnfNV14qMEFPrcnS/abc', safe='')}"
        "&mac=A1B2C3D4E5F6&model=WYZECP1_JEF&subtype=hd&dtls=true"
    )
    assert source.startswith("wyze://192.168.1.50?uid=")
    assert "enr=mnfNV14qMEFPrcnS%2Fabc" in source


def test_build_stream_source_requires_identity_fields() -> None:
    adapter = WyzeNativeAdapter(config=WyzeConfig())
    with pytest.raises(WyzeStreamSourceError):
        adapter.build_stream_source({"ip": "192.168.1.50", "mac": "A1B2C3D4E5F6"})


def test_build_stream_source_reads_nested_device_params() -> None:
    adapter = WyzeNativeAdapter(config=WyzeConfig())
    source = adapter.build_stream_source(
        {
            "mac": "A1B2C3D4E5F6",
            "product_model": "WYZECP1_JEF",
            "enr": "mnfNV14qMEFPrcnS/abc",
            "device_params": {"ip": "192.168.1.50", "p2p_id": "UPE2UNBNWDFAKE01AB"},
        }
    )

    assert source.startswith("wyze://192.168.1.50?uid=UPE2UNBNWDFAKE01AB")
    assert "enr=mnfNV14qMEFPrcnS%2Fabc" in source


async def test_capture_snapshot_writes_real_jpeg(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    adapter = WyzeNativeAdapter(config=WyzeConfig(), stream_manager=manager, cameras=[_camera()])

    path = await adapter.capture_snapshot("office_cam")

    assert path.endswith(".jpg")
    assert Path(path).read_bytes() == JPEG
    assert manager.ensured and manager.ensured[0][0] == "office_cam"
    assert manager.ensured[0][1].startswith("wyze://")


async def test_capture_snapshot_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(frame_error=MediaCaptureError("TUTK discovery timed out"))
    adapter = WyzeNativeAdapter(config=WyzeConfig(), stream_manager=manager, cameras=[_camera()])

    with pytest.raises(MediaCaptureError):
        await adapter.capture_snapshot("office_cam")
    assert list(tmp_path.iterdir()) == []


async def test_record_clip_writes_real_mp4(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager()
    adapter = WyzeNativeAdapter(config=WyzeConfig(), stream_manager=manager, cameras=[_camera()])

    path = await adapter.record_clip("office_cam", 5.0)

    assert path.endswith(".mp4")
    assert Path(path).read_bytes() == MP4
    assert b"ftyp" in Path(path).read_bytes()[:64]


async def test_record_clip_failure_writes_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    manager = FakeStreamManager(clip_error=MediaCaptureError("go2rtc returned no MP4"))
    adapter = WyzeNativeAdapter(config=WyzeConfig(), stream_manager=manager, cameras=[_camera()])

    with pytest.raises(MediaCaptureError):
        await adapter.record_clip("office_cam", 5.0)
    assert list(tmp_path.iterdir()) == []


async def test_ptz_move_uses_rotary_degree_cloud_action() -> None:
    client = FakePtzClient()
    ptz = WyzeTutkPtzAdapter(config=WyzeConfig(), client=client)

    result = await ptz.move("office_cam", 0.1, 0.0, 0.0, 0.2)

    assert result["status"] == "ok"
    assert client.calls == [
        ("office_cam", "rotary_degree", {"horizontal": 9, "vertical": 0, "speed": 2})
    ]
    assert ptz.last_command == "move"
    assert ptz.last_speed == pytest.approx(0.2)
    position = await ptz.get_position("office_cam")
    assert position["source"] == "last_command"
    assert ptz.last_command == "move"


async def test_ptz_step_uses_rotary_action() -> None:
    client = FakePtzClient()
    ptz = WyzeTutkPtzAdapter(config=WyzeConfig(), client=client)

    await ptz.step("office_cam", "right", speed=0.5)

    assert client.calls == [
        ("office_cam", "rotary_action", {"horizontal": 2, "vertical": 0, "speed": 5})
    ]
    assert ptz.last_command == "step"
    assert ptz.last_speed == pytest.approx(0.5)


async def test_ptz_zoom_only_is_explicitly_unsupported() -> None:
    client = FakePtzClient()
    ptz = WyzeTutkPtzAdapter(config=WyzeConfig(), client=client)

    result = await ptz.move("office_cam", 0.0, 0.0, 0.5, 0.4)

    assert result["status"] == "unsupported"
    assert client.calls == []
    assert (await ptz.get_position("office_cam"))["source"] == "last_command"


async def test_ptz_stop_sends_zero_rotary_action() -> None:
    client = FakePtzClient()
    ptz = WyzeTutkPtzAdapter(config=WyzeConfig(), client=client)

    await ptz.stop("office_cam")

    assert client.calls[0][1] == "rotary_action"
    assert client.calls[0][2]["horizontal"] == 0
    assert client.calls[0][2]["vertical"] == 0
    assert ptz.last_command == "stop"


async def test_ptz_presets_report_unsupported_instead_of_faking_success() -> None:
    ptz = WyzeTutkPtzAdapter(config=WyzeConfig(), client=FakePtzClient())

    assert (await ptz.set_preset("office_cam", "gate"))["status"] == "unsupported"
    assert (await ptz.list_presets("office_cam"))["presets"] == []
    assert (await ptz.goto_preset("office_cam", "gate"))["status"] == "unsupported"


async def test_connection_returns_real_devices() -> None:
    raw = FakeWyzeRawClient(cameras=[WYZE_DEVICE, {**WYZE_DEVICE, "mac": "AAA", "nickname": "B"}])
    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=raw)

    result = await adapter.test_connection()

    assert result["reachable"] is True
    assert result["provider"] == "wyze"
    assert len(result["devices"]) == 2
    assert result["devices"][0]["native_provider"] == "wyze"
    assert result["error"] is None


async def test_connection_reports_failure_instead_of_claiming_reachable() -> None:
    class BrokenClient:
        cameras = SimpleNamespace(_list_cameras=lambda: (_ for _ in ()).throw(RuntimeError("401")))

    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=BrokenClient())

    result = await adapter.test_connection()

    assert result["reachable"] is False
    assert "401" in (result["error"] or "")


async def test_discover_devices_maps_raw_cameras() -> None:
    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=FakeWyzeRawClient([WYZE_DEVICE]))

    devices = await adapter.discover_devices()

    assert devices[0]["id"] == "A1B2C3D4E5F6"
    assert devices[0]["p2p_id"] == "UPE2UNBNWDFAKE01AB"
    assert devices[0]["stream_source"].startswith("wyze://")


async def test_run_action_success_and_failure() -> None:
    raw = FakeWyzeRawClient([WYZE_DEVICE])
    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=raw, cameras=[_camera()])
    adapter.cache_devices([{**WYZE_DEVICE}])

    result = await adapter.run_action(
        "office_cam", "rotary_degree", {"horizontal": 9, "vertical": 0, "speed": 1}
    )

    assert result["status"] == "ok"
    assert raw.api.calls[0]["mac"] == "A1B2C3D4E5F6"
    assert raw.api.calls[0]["provider_key"] == "WYZECP1_JEF"

    failing = FakeWyzeRawClient(
        [WYZE_DEVICE], action_data={"code": "0", "msg": "FAILED", "data": {}}
    )
    failing_adapter = WyzeNativeAdapter(config=WyzeConfig(), client=failing, cameras=[_camera()])
    failing_adapter.cache_devices([{**WYZE_DEVICE}])
    with pytest.raises(WyzeApiError):
        await failing_adapter.run_action("office_cam", "rotary_degree", {})


async def test_poll_events_maps_wyze_event_fields() -> None:
    now = datetime.now(UTC)
    event = SimpleNamespace(
        id="evt-1",
        mac="A1B2C3D4E5F6",
        time=now,
        alarm_type=SimpleNamespace(description="Motion"),
        tags=[SimpleNamespace(description="Person"), SimpleNamespace(description="Vehicle")],
        category="motion",
        parameters={},
    )
    raw = FakeWyzeRawClient([WYZE_DEVICE], events=[event])
    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=raw, cameras=[_camera()])
    adapter.cache_devices([{**WYZE_DEVICE}])

    payloads = await adapter.poll_events("office_cam", since=None)

    assert payloads[0]["event_id"] == "evt-1"
    assert payloads[0]["mac"] == "A1B2C3D4E5F6"
    assert payloads[0]["alarm_type"] == "Motion"
    assert payloads[0]["ai_tags"] == ["Person", "Vehicle"]


def test_as_utc_handles_int_epoch_millis() -> None:
    assert _wyze_as_utc(1700000000000) == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


async def test_poll_events_handles_epoch_ms_time() -> None:
    event = SimpleNamespace(
        id="evt-ms",
        mac="A1B2C3D4E5F6",
        time=1700000000000,
        alarm_type=SimpleNamespace(description="Motion"),
        tags=[],
        category="motion",
        parameters={},
    )
    raw = FakeWyzeRawClient([WYZE_DEVICE], events=[event])
    adapter = WyzeNativeAdapter(config=WyzeConfig(), client=raw, cameras=[_camera()])
    adapter.cache_devices([{**WYZE_DEVICE}])

    payloads = await adapter.poll_events("office_cam", since=None)

    assert payloads[0]["timestamp"] == datetime.fromtimestamp(1700000000, tz=UTC).isoformat()
