"""Lane-B PTZ over the ``camera-position::*`` IoT namespace (#71).

The official app drives pan/tilt through the richer ``camera-position``
capability rather than the ``rotary_*`` legacy actions that Lane A
(:class:`lookout.adapters.wyze_native.WyzeTutkPtzAdapter`) uses. This module
implements that command surface behind :class:`lookout.ports.egress.PtzControlPort`
without changing the port:

- ``move-position`` (relative nudge, fixed ``step: 1000``),
- ``set-position`` (absolute, degrees),
- ``get-position`` (decoded and normalised to ``[-1, 1]``),
- ``stop-move-position``,
- ``goto-waypoint`` (numeric preset index, or ``"home"``).

``move-position-ex``, ``reset-position``, ``edit-waypoint`` and
``moving-speed`` are deliberately *not* implemented: they have no
``PtzControlPort`` method, and the write shapes for ``edit-waypoint`` /
``moving-speed`` have not been captured on device.

Transport seam: the adapter holds protocol knowledge (constants, direction
enum, ``step=1000``, position range) and delegates only *how* a command leaves
the process to a :class:`CameraPositionTransport`:

- :class:`CloudCameraPositionTransport` — ``POST
  /device-management/api/action/run_action`` with the ``camera-position``
  capability, signed by :class:`~lookout.adapters.wyze_signing.WyzeSigner`.
  This is the shipped transport.
- :class:`DataChannelCameraPositionTransport` — the protocol-exact RTC
  data-channel envelope ``{cmd, tid, ts, action, params}`` behind an injected
  async sender, so a future Agora session can be plugged in without touching
  this module.

The cloud envelope is **unverified on hardware** until the operator's
on-device smoke check; if it differs, only
:meth:`CloudCameraPositionTransport.run_action` needs to change.

Unsupported commands return explicit ``{"status": "unsupported", ...}``
values rather than raising, matching Lane A so :class:`PtzControlService`
audits them as successful no-ops. Transport/protocol failures raise
:class:`WyzeCameraPositionError` (``status_code = 502``), which the existing
``/api/cameras/{id}/ptz`` error mapper already honours.
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lookout.adapters.wyze_device_identity import resolve_wyze_device_identity
from lookout.ports.egress import PtzControlPort

if TYPE_CHECKING:
    from lookout.adapters.wyze_iot_cloud import WyzeIotCloudAdapter

CAPABILITY = "camera-position"
MOVE_POSITION = "camera-position::move-position"
SET_POSITION = "camera-position::set-position"
GET_POSITION = "camera-position::get-position"
STOP_MOVE_POSITION = "camera-position::stop-move-position"
GOTO_WAYPOINT = "camera-position::goto-waypoint"

#: Fixed relative-nudge magnitude mandated by the protocol notes.
MOVE_STEP = 1000
#: ``get-position`` replies in degrees over a ``±90`` range.
POSITION_LIMIT = 90.0

_DIRECTIONS = frozenset({"up", "down", "left", "right"})
_POSITION_KEYS = frozenset({"params", "data", "result", "response"})


class WyzeCameraPositionError(RuntimeError):
    """Raised when a ``camera-position::*`` command fails or returns no position."""

    status_code = 502


@runtime_checkable
class CameraPositionTransport(Protocol):
    """How one ``camera-position::*`` action leaves the process."""

    async def run_action(
        self, camera_id: str, action: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send ``action`` for ``camera_id`` and return the decoded response body."""
        ...


def _clamp_unit(value: Any) -> float:
    """Clamp a normalised PTZ input to ``[-1, 1]``; unparseable input is zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(-1.0, min(1.0, number))


def _normalize(value: float) -> float:
    """Map a ``±90`` degree value onto ``[-1, 1]`` (out-of-range values clamp)."""
    return _clamp_unit(float(value) / POSITION_LIMIT)


def _extract_position(payload: Any) -> tuple[float, float] | None:
    """Find the first ``{horizontal, vertical}`` map in a response payload.

    Only the documented ``params`` / ``data`` / ``result`` / ``response``
    containers are walked, breadth-first, so an unrelated sibling numeric pair
    is never mistaken for the camera position.
    """
    if not isinstance(payload, Mapping):
        return None
    queue: list[Any] = [payload]
    visited: set[int] = set()
    while queue:
        node = queue.pop(0)
        if not isinstance(node, Mapping) or id(node) in visited:
            continue
        visited.add(id(node))
        horizontal = node.get("horizontal")
        vertical = node.get("vertical")
        if (
            isinstance(horizontal, int | float)
            and not isinstance(horizontal, bool)
            and isinstance(vertical, int | float)
            and not isinstance(vertical, bool)
        ):
            return float(horizontal), float(vertical)
        for key in _POSITION_KEYS:
            child = node.get(key)
            if isinstance(child, Mapping):
                queue.append(child)
    return None


class CloudCameraPositionTransport:
    """``camera-position::*`` actions over the device-management ``run_action`` REST lane."""

    def __init__(
        self,
        *,
        iot_cloud: WyzeIotCloudAdapter,
        cameras: Any = (),
        device_resolver: Callable[[str], Any] | None = None,
        native_adapter: Any = None,
        user_id: str | None = None,
    ) -> None:
        self.iot_cloud = iot_cloud
        self._cameras_source = cameras
        self.device_resolver = device_resolver
        self.native_adapter = native_adapter
        self._user_id = user_id

    def _identity(self, camera_id: str) -> tuple[str, str]:
        identity = resolve_wyze_device_identity(
            str(camera_id),
            cameras=self._cameras_source,
            device_resolver=self.device_resolver,
            native_adapter=self.native_adapter,
        )
        if identity is None:
            raise WyzeCameraPositionError(f"Camera '{camera_id}' has no Wyze device identity")
        return identity.device_id, identity.model

    def _request_user_id(self) -> str | None:
        cloud_user_id = getattr(self.iot_cloud, "user_id", None)
        if cloud_user_id:
            return str(cloud_user_id)
        native = self.native_adapter
        if native is not None:
            native_user_id = getattr(native, "user_id", None)
            if native_user_id:
                return str(native_user_id)
        if self._user_id is not None:
            return str(self._user_id)
        return None

    async def run_action(
        self, camera_id: str, action: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        device_id, model = self._identity(camera_id)
        result = await self.iot_cloud.run_action(
            device_id,
            model,
            user_id=self._request_user_id(),
            capability=CAPABILITY,
            properties=[{"separator": "::", "prop": action, "value": dict(params or {})}],
        )
        if isinstance(result, Mapping):
            response = result.get("response")
            if isinstance(response, Mapping):
                return dict(response)
            if response is None:
                return dict(result)
            return {"raw": response}
        return {"raw": result}


class DataChannelCameraPositionTransport:
    """Protocol-exact RTC data-channel transport behind an injected sender."""

    def __init__(self, *, send: Callable[[str, dict[str, Any]], Awaitable[Any]]) -> None:
        self._send = send
        self._tids = itertools.count(1)

    @staticmethod
    def build_envelope(
        action: str,
        params: Mapping[str, Any] | None = None,
        *,
        tid: int,
        ts: int,
    ) -> dict[str, Any]:
        """Build the ``{cmd, tid, ts, action, params}`` data-channel envelope."""
        return {
            "cmd": "run_action",
            "tid": int(tid),
            "ts": int(ts),
            "action": action,
            "params": dict(params or {}),
        }

    async def run_action(
        self, camera_id: str, action: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        envelope = self.build_envelope(
            action,
            params,
            tid=next(self._tids),
            ts=int(time.time() * 1000),
        )
        result = await self._send(str(camera_id), envelope)
        if not isinstance(result, Mapping):
            raise WyzeCameraPositionError(f"Data-channel '{action}' response was not a mapping")
        code = result.get("result")
        if code is not None and code != 0:
            raise WyzeCameraPositionError(f"Data-channel '{action}' failed: result={code}")
        return dict(result)


class WyzeAgoraPtzAdapter(PtzControlPort):
    """Lane-B ``PtzControlPort`` over the ``camera-position::*`` namespace."""

    def __init__(self, *, transport: CameraPositionTransport) -> None:
        self.transport = transport
        self.last_command: str | None = None
        self.last_speed: float | None = None

    @staticmethod
    def _clamp_speed(speed: float) -> float:
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 0.5
        if not math.isfinite(value):
            value = 0.5
        return max(0.0, min(1.0, value))

    async def _command(
        self, camera_id: str, action: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = await self.transport.run_action(str(camera_id), action, params)
        return {"status": "ok", "action_key": action, "response": payload}

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> Any:
        self.last_command = "move"
        self.last_speed = self._clamp_speed(speed)
        pan_value = _clamp_unit(pan)
        tilt_value = _clamp_unit(tilt)
        zoom_value = _clamp_unit(zoom)
        if abs(pan_value) < 1e-9 and abs(tilt_value) < 1e-9:
            if abs(zoom_value) > 1e-9:
                return {
                    "status": "unsupported",
                    "reason": "zoom not exposed by the camera-position namespace",
                }
            return {"status": "ok", "action_key": None, "response": None}
        return await self._command(
            camera_id,
            SET_POSITION,
            {
                "horizontal": int(round(pan_value * POSITION_LIMIT)),
                "vertical": int(round(tilt_value * POSITION_LIMIT)),
            },
        )

    async def stop(self, camera_id: str, pan_tilt: bool = True, zoom: bool = True) -> Any:
        self.last_command = "stop"
        if not pan_tilt and not zoom:
            return {"status": "ok", "action_key": None, "response": None}
        return await self._command(camera_id, STOP_MOVE_POSITION, {})

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> Any:
        self.last_command = "step"
        self.last_speed = self._clamp_speed(speed)
        normalized = str(direction).lower()
        if normalized == "stop":
            return await self._command(camera_id, STOP_MOVE_POSITION, {})
        if normalized not in _DIRECTIONS:
            return {
                "status": "unsupported",
                "reason": f"Unknown PTZ direction '{direction}'",
            }
        return await self._command(
            camera_id,
            MOVE_POSITION,
            {"direction": normalized, "step": MOVE_STEP},
        )

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> Any:
        self.last_command = "goto_preset"
        self.last_speed = self._clamp_speed(speed)
        name = str(preset).strip()
        if name.lower() == "home":
            return await self._command(camera_id, GOTO_WAYPOINT, {"home": True, "index": 0})
        try:
            index = int(name)
        except (TypeError, ValueError):
            return {
                "status": "unsupported",
                "reason": "camera-position::goto-waypoint only exposes numeric indices",
            }
        return await self._command(camera_id, GOTO_WAYPOINT, {"home": False, "index": index})

    async def set_preset(self, camera_id: str, name: str) -> Any:
        self.last_command = "set_preset"
        return {
            "status": "unsupported",
            "reason": (
                "camera-position::edit-waypoint write shape is not captured; refusing to speculate"
            ),
        }

    async def list_presets(self, camera_id: str) -> Any:
        self.last_command = "list_presets"
        return {
            "status": "unsupported",
            "presets": [],
            "reason": (
                "camera-position waypoints are numeric indices; "
                "the listing property is not captured"
            ),
        }

    async def get_position(self, camera_id: str) -> Any:
        self.last_command = "get_position"
        payload = await self.transport.run_action(str(camera_id), GET_POSITION, {})
        position = _extract_position(payload)
        if position is None:
            raise WyzeCameraPositionError(
                f"Wyze camera '{camera_id}' returned no camera-position reading"
            )
        horizontal, vertical = position
        return {
            "horizontal": _normalize(horizontal),
            "vertical": _normalize(vertical),
            "source": GET_POSITION,
        }


__all__ = [
    "CAPABILITY",
    "GET_POSITION",
    "GOTO_WAYPOINT",
    "MOVE_POSITION",
    "MOVE_STEP",
    "POSITION_LIMIT",
    "SET_POSITION",
    "STOP_MOVE_POSITION",
    "CameraPositionTransport",
    "CloudCameraPositionTransport",
    "DataChannelCameraPositionTransport",
    "WyzeAgoraPtzAdapter",
    "WyzeCameraPositionError",
]
