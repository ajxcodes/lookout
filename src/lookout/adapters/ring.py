"""Ring cloud media recording and snapshot adapter."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from lookout.adapters.tempfile import TempMediaManager
from lookout.config.schema import RingConfig
from lookout.ports.egress import VideoRecorderPort

logger = logging.getLogger(__name__)


class RingAdapter(VideoRecorderPort):
    """Ring cloud API adapter implementing VideoRecorderPort."""

    def __init__(
        self,
        config: RingConfig | None = None,
        temp_manager: TempMediaManager | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or RingConfig()
        self.temp_manager = temp_manager or TempMediaManager()
        self._external_client = client

    def _clean_camera_id(self, camera_id: str) -> str:
        """Strip optional ring:// URI scheme prefix."""
        if camera_id.startswith("ring://"):
            return camera_id.removeprefix("ring://")
        return camera_id

    def _get_headers(self) -> dict[str, str]:
        """Generate authentication headers."""
        headers: dict[str, str] = {}
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield active HTTP client session."""
        if self._external_client is not None:
            yield self._external_client
        else:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                headers=self._get_headers(),
                follow_redirects=True,
            ) as client:
                yield client

    async def record_clip(self, camera_id: str, duration_seconds: float) -> str:
        """Download latest recorded clip from Ring cloud with snapshot fallback."""
        device_id = self._clean_camera_id(camera_id)
        api_base = self.config.api_url.rstrip("/")

        try:
            async with self._get_client() as client:
                # 1. Try direct recording endpoint
                recording_url = f"{api_base}/clients_api/doorbots/{device_id}/recording"
                response = await client.get(recording_url)

                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        data: dict[str, Any] = response.json()
                        download_url = data.get("url") or data.get("recording_url")
                        if download_url:
                            response = await client.get(download_url)
                            response.raise_for_status()
                    # Write video content
                    temp_file = self.temp_manager.create_temp_file(suffix=".mp4")
                    Path(temp_file).write_bytes(response.content)
                    return str(temp_file)

                # 2. Try history query for latest event
                history_url = f"{api_base}/clients_api/doorbots/{device_id}/history"
                h_resp = await client.get(history_url)
                if h_resp.status_code == 200:
                    history = h_resp.json()
                    if isinstance(history, list) and len(history) > 0:
                        ding_id = history[0].get("id") or history[0].get("ding_id")
                        if ding_id:
                            ding_url = f"{api_base}/clients_api/dings/{ding_id}/recording"
                            d_resp = await client.get(ding_url)
                            if d_resp.status_code == 200:
                                temp_file = self.temp_manager.create_temp_file(suffix=".mp4")
                                Path(temp_file).write_bytes(d_resp.content)
                                return str(temp_file)

                # If no successful download, raise to trigger snapshot fallback
                status = response.status_code
                raise RuntimeError(
                    f"Ring cloud recording unavailable for {camera_id} (status {status})"
                )

        except Exception as err:
            logger.warning(
                "Ring cloud clip resolution failed for '%s' (%s); falling back to snapshot",
                camera_id,
                err,
            )
            return await self.capture_snapshot(camera_id)

    async def capture_snapshot(self, camera_id: str) -> str:
        """Capture latest still snapshot image from Ring cloud."""
        device_id = self._clean_camera_id(camera_id)
        api_base = self.config.api_url.rstrip("/")
        snapshot_url = f"{api_base}/clients_api/doorbots/{device_id}/snapshot"

        async with self._get_client() as client:
            response = await client.get(snapshot_url)
            response.raise_for_status()

            temp_file = self.temp_manager.create_temp_file(suffix=".jpg")
            Path(temp_file).write_bytes(response.content)
            return str(temp_file)
