"""Routing tests for the two-lane Wyze motion-tag adapter (#111)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lookout.adapters.wyze_hl import HlControlError, WyzeHlMotionTagAdapter
from lookout.adapters.wyze_motion_tag_router import WyzeMotionTagRouter
from lookout.config.schema import CameraConfig
from lookout.domain.models import MotionTagState, MotionTagWriteResult
from lookout.ports.egress import MotionTagPort


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeLane:
    """Model-gated lane that records only real I/O (owned-model calls)."""

    def __init__(
        self,
        *,
        models: set[str],
        camera_models: dict[str, str],
        state: MotionTagState | None = None,
        write: MotionTagWriteResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.models = set(models)
        self.camera_models = camera_models
        self.state = state or MotionTagState(supported=False, enabled=None)
        self.write = write or MotionTagWriteResult(supported=False, enabled=None, raw={})
        self.error = error
        self.calls = 0
        self.reads = 0
        self.writes = 0

    def _owned(self, camera_id: str) -> bool:
        return self.camera_models.get(str(camera_id)) in self.models

    async def get_motion_tag(self, camera_id: str) -> MotionTagState:
        if self.error is not None:
            self.calls += 1
            raise self.error
        if not self._owned(camera_id):
            return MotionTagState(supported=False, enabled=None)
        self.calls += 1
        self.reads += 1
        return self.state

    async def set_motion_tag(self, camera_id: str, enabled: bool) -> MotionTagWriteResult:
        if self.error is not None:
            self.calls += 1
            raise self.error
        if not self._owned(camera_id):
            return MotionTagWriteResult(supported=False, enabled=None, raw={})
        self.calls += 1
        self.writes += 1
        return self.write


class RecordingTransport:
    def __init__(self, value: int = 2) -> None:
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


def test_router_routes_cloud_model_to_cloud_lane() -> None:
    camera_models = {"cam1": "HL_CAM4"}
    cloud = FakeLane(
        models={"HL_CAM4"},
        camera_models=camera_models,
        state=MotionTagState(supported=True, enabled=True),
    )
    hl = FakeLane(
        models={"HL_PAN3"},
        camera_models=camera_models,
        state=MotionTagState(supported=True, enabled=False),
    )
    router = WyzeMotionTagRouter(cloud=cloud, hl=hl)

    state = _run(router.get_motion_tag("cam1"))

    assert state == MotionTagState(supported=True, enabled=True)
    assert cloud.reads == 1
    assert hl.calls == 0


def test_router_routes_hl_model_to_hl_lane() -> None:
    camera_models: dict[str, str] = {}
    cloud = FakeLane(models={"HL_CAM4"}, camera_models=camera_models)
    cameras: list[CameraConfig] = []
    transport = RecordingTransport(value=2)
    hl = WyzeHlMotionTagAdapter(transport=transport, cameras=lambda: cameras)
    router = WyzeMotionTagRouter(cloud=cloud, hl=hl)

    # Appended after construction: the callable camera source must still resolve.
    cameras.append(
        CameraConfig(
            id="mochi_pan_cam",
            name="Mochi Pan",
            stream_url="wyze://?mac=AA:BB:CC:DD:EE:FF&model=HL_PAN3&subtype=hd",
            native_provider="wyze",
            native_device_id="AA:BB:CC:DD:EE:FF",
            native_model="HL_PAN3",
        )
    )
    camera_models["mochi_pan_cam"] = "HL_PAN3"

    cloud_state = _run(cloud.get_motion_tag("mochi_pan_cam"))
    state = _run(router.get_motion_tag("mochi_pan_cam"))
    written = _run(router.set_motion_tag("mochi_pan_cam", True))

    assert cloud_state == MotionTagState(supported=False, enabled=None)
    assert cloud.calls == 0
    assert state == MotionTagState(supported=True, enabled=False)
    assert written == MotionTagWriteResult(
        supported=True,
        enabled=True,
        raw={"transport": "hl", "property": "motion_tagging", "value": 1},
    )
    assert transport.writes == [("mochi_pan_cam", "motion_tagging", 1)]


def test_router_keeps_original_pan_unsupported_without_io() -> None:
    camera_models = {"office_cam": "WYZECP1_JEF"}
    cloud = FakeLane(models={"HL_CAM4"}, camera_models=camera_models)
    hl = FakeLane(models={"HL_PAN3"}, camera_models=camera_models)
    router = WyzeMotionTagRouter(cloud=cloud, hl=hl)

    state = _run(router.get_motion_tag("office_cam"))
    written = _run(router.set_motion_tag("office_cam", True))

    assert state == MotionTagState(supported=False, enabled=None)
    assert written == MotionTagWriteResult(supported=False, enabled=None, raw={})
    assert cloud.calls == 0
    assert hl.calls == 0


def test_router_propagates_lane_error() -> None:
    camera_models = {"mochi_pan_cam": "HL_PAN3"}
    cloud = FakeLane(
        models={"HL_CAM4"},
        camera_models=camera_models,
        error=HlControlError("bridge timeout"),
    )
    spy = FakeLane(models={"HL_PAN3"}, camera_models=camera_models)
    router = WyzeMotionTagRouter(cloud=cloud, hl=spy)

    with pytest.raises(HlControlError) as excinfo:
        _run(router.set_motion_tag("mochi_pan_cam", True))

    assert excinfo.value.status_code == 502
    assert spy.calls == 0
    assert isinstance(WyzeMotionTagRouter(), MotionTagPort) is True
