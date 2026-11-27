"""Ordered motion-tag lane router.

Cameras are served by exactly one lane: the cloud device-management lane for
``HL_CAM4``-class models first, then the HL/Hualai camera-property lane for cam
v3 / Pan v3. Each lane self-gates on its own allowlist, so the router returns
the first lane that reports ``supported: true`` and otherwise the last explicit
unsupported result. A lane error is never swallowed.
"""

from __future__ import annotations

from lookout.domain.models import MotionTagState, MotionTagWriteResult
from lookout.ports.egress import MotionTagPort


class WyzeMotionTagRouter:
    """Compose the cloud and HL motion-tag lanes behind one ``MotionTagPort``."""

    def __init__(
        self, *, cloud: MotionTagPort | None = None, hl: MotionTagPort | None = None
    ) -> None:
        self.cloud = cloud
        self.hl = hl

    @property
    def lanes(self) -> list[MotionTagPort]:
        """Return the configured lanes in routing order (cloud, then HL)."""
        return [lane for lane in (self.cloud, self.hl) if lane is not None]

    async def get_motion_tag(self, camera_id: str) -> MotionTagState:
        result = MotionTagState(supported=False, enabled=None)
        for lane in self.lanes:
            state = await lane.get_motion_tag(camera_id)
            if getattr(state, "supported", False):
                return state
            result = state
        return result

    async def set_motion_tag(self, camera_id: str, enabled: bool) -> MotionTagWriteResult:
        result = MotionTagWriteResult(supported=False, enabled=None, raw={})
        for lane in self.lanes:
            written = await lane.set_motion_tag(camera_id, enabled)
            if getattr(written, "supported", False):
                return written
            result = written
        return result


__all__ = ["WyzeMotionTagRouter"]
