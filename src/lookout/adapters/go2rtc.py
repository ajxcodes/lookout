"""go2rtc media recording and snapshot adapter."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import Go2RtcConfig, resolve_record_audio
from lookout.ports.egress import VideoRecorderPort

logger = logging.getLogger(__name__)


class Go2RtcAdapter(VideoRecorderPort):
    """go2rtc REST/RTSP stream adapter implementing VideoRecorderPort."""

    def __init__(
        self,
        config: Go2RtcConfig | None = None,
        temp_manager: TempMediaManager | None = None,
        client: httpx.AsyncClient | None = None,
        lookout_config: Any | None = None,
    ) -> None:
        self.config = config or Go2RtcConfig()
        self.temp_manager = temp_manager or TempMediaManager()
        self._external_client = client
        self.lookout_config = lookout_config
        self.record_audio = resolve_record_audio(None, self.lookout_config)

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield active HTTP client session."""
        if self._external_client is not None:
            yield self._external_client
        else:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                yield client

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        """Record video clip via go2rtc stream API with snapshot fallback."""
        url = f"{self.config.api_url.rstrip('/')}/api/stream.mp4"
        params: dict[str, str | float] = {"src": camera_id, "duration": duration_seconds}
        if resolve_record_audio(str(camera_id), self.lookout_config):
            params["mp4"] = "all"

        try:
            async with (
                self._get_client() as client,
                client.stream("GET", url, params=params) as response,
            ):
                response.raise_for_status()
                temp_file = self.temp_manager.create_temp_file(suffix=".mp4")
                with open(temp_file, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
                return str(temp_file)
        except (httpx.HTTPError, OSError) as err:
            logger.warning(
                "go2rtc video stream unavailable for camera '%s' (%s); falling back to snapshot",
                camera_id,
                err,
            )
            return await self.capture_snapshot(camera_id)

    async def capture_snapshot(self, camera_id: str) -> str:
        """Extract still JPEG snapshot via go2rtc frame API."""
        url = f"{self.config.api_url.rstrip('/')}/api/frame.jpeg"
        params = {"src": camera_id}

        async with self._get_client() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            temp_file = self.temp_manager.create_temp_file(suffix=".jpg")
            Path(temp_file).write_bytes(response.content)
            return str(temp_file)
