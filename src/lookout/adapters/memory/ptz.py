"""Mock PTZ control adapter for testing and standalone execution."""

from typing import Any

from lookout.ports.egress import PtzControlPort


class MockPtzControlAdapter(PtzControlPort):
    """Synthetic PTZ adapter recording calls and returning mock responses."""

    def __init__(self) -> None:
        self.last_speed: float | None = None
        self.calls: list[dict[str, Any]] = []

    async def move(
        self, camera_id: str, pan: float, tilt: float, zoom: float, speed: float = 0.5
    ) -> dict[str, Any]:
        self.last_speed = speed
        self.calls.append(
            {
                "action": "move",
                "camera_id": camera_id,
                "pan": pan,
                "tilt": tilt,
                "zoom": zoom,
                "speed": speed,
            }
        )
        return {"status": "ok", "camera_id": camera_id}

    async def stop(
        self, camera_id: str, pan_tilt: bool = True, zoom: bool = True
    ) -> dict[str, Any]:
        self.calls.append(
            {"action": "stop", "camera_id": camera_id, "pan_tilt": pan_tilt, "zoom": zoom}
        )
        return {"status": "ok", "camera_id": camera_id}

    async def step(
        self, camera_id: str, direction: str, amount: float = 0.1, speed: float = 0.5
    ) -> dict[str, Any]:
        self.last_speed = speed
        self.calls.append(
            {
                "action": "step",
                "camera_id": camera_id,
                "direction": direction,
                "amount": amount,
                "speed": speed,
            }
        )
        return {"status": "ok", "camera_id": camera_id}

    async def goto_preset(self, camera_id: str, preset: str, speed: float = 0.5) -> dict[str, Any]:
        self.last_speed = speed
        self.calls.append(
            {"action": "goto_preset", "camera_id": camera_id, "preset": preset, "speed": speed}
        )
        return {"status": "ok", "camera_id": camera_id}

    async def set_preset(self, camera_id: str, name: str) -> dict[str, Any]:
        self.calls.append({"action": "set_preset", "camera_id": camera_id, "name": name})
        return {"status": "ok", "camera_id": camera_id}

    async def list_presets(self, camera_id: str) -> list[Any]:
        self.calls.append({"action": "list_presets", "camera_id": camera_id})
        return []

    async def get_position(self, camera_id: str) -> dict[str, Any]:
        self.calls.append({"action": "get_position", "camera_id": camera_id})
        return {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}
