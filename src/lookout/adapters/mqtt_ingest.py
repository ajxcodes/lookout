"""Native MQTT ingress: ring-mqtt motion/ding and wyze-bridge audio -> Lookout events."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lookout.config.schema import CameraConfig, LookoutConfig
from lookout.domain.models import CameraEvent

logger = logging.getLogger(__name__)

_CONNECT = 0x10
_CONNACK = 0x20
_PUBLISH = 0x30
_SUBSCRIBE = 0x82
_PINGREQ = 0xC0
_PROTOCOL_LEVEL = 4
_RETAINED_GRACE_SECONDS = 3.0
_RECONNECT_MAX_SECONDS = 30.0
_EVENT_SNAPSHOT_DIR = ("snapshots", "events")
_PET_AUDIO_LABELS = frozenset(
    {
        "bark",
        "whimper",
        "crying",
        "crying, sobbing",
        "alarm",
        "smoke_alarm",
        "glass_break",
        "scream",
    }
)


def _encode_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


def _encode_remaining(length: int) -> bytes:
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        out.append(digit | 0x80 if length else digit)
        if not length:
            return bytes(out)


class MiniMqttClient:
    """Minimal MQTT 3.1.1 subscriber (QoS 0) so no extra dependency is required."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: str = "lookout",
        username: str | None = None,
        password: str | None = None,
        keepalive: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ping_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        flags = 0x02
        payload = _encode_str(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _encode_str(self.username)
            if self.password:
                flags |= 0x40
                payload += _encode_str(self.password)
        variable = (
            _encode_str("MQTT")
            + bytes([_PROTOCOL_LEVEL, flags])
            + self.keepalive.to_bytes(2, "big")
        )
        body = variable + payload
        await self._send(bytes([_CONNECT]) + _encode_remaining(len(body)) + body)
        header, data = await self._read_packet()
        if header & 0xF0 != _CONNACK or len(data) < 2 or data[1] != 0:
            raise ConnectionError(f"MQTT connection refused (packet={header:#x}, data={data!r})")
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def subscribe(self, topics: list[str]) -> None:
        body = (1).to_bytes(2, "big")
        for topic in topics:
            body += _encode_str(topic) + b"\x00"
        await self._send(bytes([_SUBSCRIBE]) + _encode_remaining(len(body)) + body)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        while True:
            header, data = await self._read_packet()
            if header & 0xF0 != _PUBLISH:
                continue
            topic_len = int.from_bytes(data[:2], "big")
            topic = data[2 : 2 + topic_len].decode("utf-8", errors="replace")
            offset = 2 + topic_len
            if (header >> 1) & 0x03:
                offset += 2
            yield topic, data[offset:]

    async def close(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ping_task
            self._ping_task = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None

    async def _send(self, packet: bytes) -> None:
        if self._writer is None:
            raise ConnectionError("MQTT client is not connected")
        self._writer.write(packet)
        await self._writer.drain()

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(max(self.keepalive / 2, 1))
            await self._send(bytes([_PINGREQ, 0]))

    async def _read_packet(self) -> tuple[int, bytes]:
        if self._reader is None:
            raise ConnectionError("MQTT client is not connected")
        header = (await self._reader.readexactly(1))[0]
        multiplier, length = 1, 0
        while True:
            digit = (await self._reader.readexactly(1))[0]
            length += (digit & 0x7F) * multiplier
            if not digit & 0x80:
                break
            multiplier *= 128
        data = await self._reader.readexactly(length) if length else b""
        return header, data


class MqttEventIngestor:
    """Subscribes to camera-bridge topics and feeds events into the processing service."""

    def __init__(
        self,
        config: LookoutConfig,
        ingest: Callable[[str, dict[str, Any]], Awaitable[CameraEvent]],
        on_event: Callable[[CameraEvent], Awaitable[None]] | None = None,
        on_clip_ready: Callable[[CameraEvent], Awaitable[None]] | None = None,
        client_factory: Callable[[], MiniMqttClient] | None = None,
    ) -> None:
        self.config = config
        self._ingest = ingest
        self._on_event = on_event
        self._on_clip_ready = on_clip_ready
        self._client_factory = client_factory or self._default_client
        self._ring_snapshots: dict[str, bytes] = {}
        self._frame_buffers: dict[str, collections.deque[bytes]] = {}
        self._pretrigger_frames: dict[str, list[bytes]] = {}
        self._last_trigger: dict[str, float] = {}
        self._ready_at = 0.0
        self._task: asyncio.Task[None] | None = None

    def _default_client(self) -> MiniMqttClient:
        cfg = self.config.mqtt
        return MiniMqttClient(cfg.host, cfg.port, "lookout", cfg.username, cfg.password)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def buffer_frame(self, camera_id: str, frame_bytes: bytes) -> None:
        """Append a frame to the per-camera rolling pre-trigger buffer."""
        camera_id = str(camera_id)
        buf = self._frame_buffers.get(camera_id)
        if buf is None:
            buf = collections.deque(maxlen=5)
            self._frame_buffers[camera_id] = buf
        if frame_bytes:
            buf.append(bytes(frame_bytes))

    def drain_pretrigger_frames(self, camera_id: str, max_frames: int = 5) -> list[bytes]:
        """Return (and clear) up to ``max_frames`` pre-trigger frames for a camera."""
        camera_id = str(camera_id)
        buf = self._frame_buffers.get(camera_id)
        if not buf:
            return []
        frames = list(buf)
        if max_frames is not None and max_frames >= 0:
            frames = frames[-max_frames:]
        buf.clear()
        return [bytes(f) for f in frames]

    def _topics(self) -> list[str]:
        ring, wyze = self.config.mqtt.ring_topic, self.config.mqtt.wyze_topic
        return [
            f"{ring}/+/camera/+/motion/state",
            f"{ring}/+/camera/+/ding/state",
            f"{ring}/+/camera/+/snapshot/image",
            f"{wyze}/+/audio",
            f"{wyze}/+/detection",
        ]

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            client = self._client_factory()
            try:
                await client.connect()
                await client.subscribe(self._topics())
                self._ready_at = time.monotonic() + _RETAINED_GRACE_SECONDS
                backoff = 1.0
                logger.info("MQTT ingest connected to %s:%s", client.host, client.port)
                async for topic, payload in client.messages():
                    try:
                        await self.handle_message(topic, payload)
                    except Exception as exc:
                        logger.warning("MQTT message handling failed for %s: %s", topic, exc)
            except asyncio.CancelledError:
                await client.close()
                raise
            except Exception as exc:
                logger.warning("MQTT ingest disconnected (%s); retrying in %.0fs", exc, backoff)
            await client.close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    def _camera_for(self, key: str) -> CameraConfig | None:
        normalized = key.replace("-", "_")
        for cam in self.config.cameras:
            if key in (cam.id, cam.mqtt_id) or normalized in (cam.id, cam.mqtt_id):
                return cam
        return None

    async def handle_message(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        ring, wyze = self.config.mqtt.ring_topic, self.config.mqtt.wyze_topic
        if parts[0] == ring and len(parts) == 6 and parts[2] == "camera":
            device, kind, leaf = parts[3], parts[4], parts[5]
            if kind == "snapshot" and leaf == "image":
                self._ring_snapshots[device] = payload
                self.buffer_frame(device, payload)
            elif (
                kind in ("motion", "ding")
                and leaf == "state"
                and payload.decode(errors="ignore").strip().upper() == "ON"
            ):
                await self._trigger(device, kind, {})
        elif parts[0] == wyze and len(parts) == 3 and parts[2] in ("audio", "detection"):
            try:
                data = json.loads(payload)
            except ValueError:
                return
            if not isinstance(data, dict):
                return
            label = (
                str(data.get("detection") or data.get("sound") or data.get("label") or "")
                .lower()
                .strip()
            )
            if label in _PET_AUDIO_LABELS:
                await self._trigger(parts[1], "audio", {"sound": label})

    async def _trigger(self, key: str, kind: str, extra: dict[str, Any]) -> None:
        if time.monotonic() < self._ready_at:
            return
        cam = self._camera_for(key)
        if cam is None or not cam.enabled:
            return
        now = time.monotonic()
        if now - self._last_trigger.get(cam.id, -1e9) < self.config.mqtt.cooldown_seconds:
            return
        self._last_trigger[cam.id] = now

        image = self._ring_snapshots.get(key)
        if image is None:
            image = await self._go2rtc_frame(cam.id)
            if image:
                self.buffer_frame(cam.id, image)
        else:
            self.buffer_frame(cam.id, image)
        # Drain pre-trigger frames from any buffer keyed to this camera (device key
        # or resolved camera id) so seeded frames are captured regardless of key.
        pre_frames: list[bytes] = []
        for cid in {str(key), str(cam.id)}:
            pre_frames.extend(self.drain_pretrigger_frames(cid))
        metadata: dict[str, Any] = {"trigger": kind, "source": "mqtt", **extra}
        metadata["pretrigger_frames"] = len(pre_frames)
        payload: dict[str, Any] = {"metadata": metadata}
        if image:
            payload["image"] = image
            payload["snapshot_uri"] = self._save_snapshot(cam.id, kind, image)
        if kind == "audio" and "sound" in extra:
            payload["audio"] = str(extra["sound"]).encode("utf-8")
        event = await self._ingest(cam.id, payload)
        if event is not None:
            if event.metadata is None:
                event.metadata = {}
            event.metadata["pretrigger_frames"] = len(pre_frames)
            # Stash pre-trigger frames for the background clip enrichment.
            self._pretrigger_frames[event.id] = list(pre_frames)
        if self._on_event is not None:
            await self._on_event(event)
        if event is not None and self._on_clip_ready is not None:
            asyncio.create_task(self._record_clip_background(cam.id, event, duration=10.0))

    async def _record_clip_background(
        self, camera_id: str, event: CameraEvent, duration: float = 10.0
    ) -> None:
        event_id = str(getattr(event, "id", "") or "")
        pre_frames = self._pretrigger_frames.pop(event_id, []) or []
        if not pre_frames:
            try:
                pre_frames = self.drain_pretrigger_frames(camera_id, max_frames=5)
            except Exception:
                pre_frames = []
        base = self.config.go2rtc.api_url.rstrip("/")
        name = f"{camera_id}_{event.id}.mp4"
        directory = Path(self.config.storage.base_path) / "events"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            clip_path = directory / name
            url = f"{base}/api/stream.mp4"

            cam = self._camera_for(camera_id)
            include_audio = (
                cam.record_audio
                if cam and cam.record_audio is not None
                else getattr(self.config.transcode, "record_audio", True)
            )

            is_on_demand = bool(cam and cam.mqtt_id)
            if is_on_demand:
                # Allow on-demand streams (e.g. Ring WebRTC signaling) time to establish producer
                await asyncio.sleep(2.5)

            primary_src = f"ffmpeg:{camera_id}#video=copy#audio=aac" if include_audio else camera_id
            params: dict[str, str | int] = {"src": primary_src, "duration": int(duration)}
            max_attempts = 3 if is_on_demand else 2
            resp: httpx.Response | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    async with httpx.AsyncClient(timeout=duration + 15.0) as client:
                        resp = await client.get(url, params=params)
                        if (resp.status_code != 200 or len(resp.content) < 1000) and include_audio:
                            # Fallback to plain video-only stream if audio transcode failed
                            fallback_params: dict[str, str | int] = {
                                "src": camera_id,
                                "duration": int(duration),
                            }
                            resp = await client.get(url, params=fallback_params)
                except httpx.HTTPError as net_err:
                    logger.debug(
                        "Clip recording request attempt %d/%d for %s failed: %s",
                        attempt,
                        max_attempts,
                        camera_id,
                        net_err,
                    )

                if resp is not None and resp.status_code == 200 and len(resp.content) > 1000:
                    break

                if attempt < max_attempts:
                    await asyncio.sleep(1.5 * attempt)

            if resp is not None and resp.status_code == 200 and len(resp.content) > 1000:
                clip_path.write_bytes(resp.content)
                event.media_uri = f"/media/events/{name}"
                logger.info(
                    "Recorded background clip for %s (%s): %s bytes -> %s (audio=%s)",
                    camera_id,
                    event.id,
                    len(resp.content),
                    event.media_uri,
                    include_audio,
                )

                # Generate lightweight animated preview GIF for mobile notifications & cards
                try:
                    from lookout.adapters.ffmpeg import FFmpegTranscoderAdapter

                    gif_name = f"{camera_id}_{event.id}.gif"
                    gif_path = directory / gif_name
                    adapter = FFmpegTranscoderAdapter(config=self.config.transcode)
                    await adapter.generate_preview_gif(
                        input_path=str(clip_path),
                        output_path=str(gif_path),
                        duration_seconds=min(self.config.transcode.gif_duration, duration),
                        fps=self.config.transcode.gif_fps,
                        width=self.config.transcode.gif_width,
                    )
                    if gif_path.is_file() and gif_path.stat().st_size > 0:
                        if event.metadata is None:
                            event.metadata = {}
                        event.metadata["gif_uri"] = f"/media/events/{gif_name}"
                        logger.info(
                            "Generated preview GIF for %s: %s",
                            event.id,
                            event.metadata["gif_uri"],
                        )
                except Exception as gif_exc:
                    logger.debug("GIF preview generation skipped for %s: %s", event.id, gif_exc)

                if event.metadata is None:
                    event.metadata = {}
                frame_samples: list[bytes] = list(pre_frames)
                # Best-effort: sample a few evenly spaced frames from the saved clip
                # via ffmpeg when available, so the second pass has post-trigger data.
                try:
                    import shutil as _shutil

                    if _shutil.which("ffmpeg") is not None:
                        sampled = await self._sample_clip_frames(str(clip_path), max_frames=5)
                        frame_samples.extend(sampled)
                except Exception:  # pragma: no cover - best effort
                    pass
                if frame_samples:
                    event.metadata["clip_frame_samples"] = frame_samples
                else:
                    event.metadata.setdefault("clip_frame_samples", [])

                if self._on_clip_ready is not None:
                    await self._on_clip_ready(event)
            else:
                status = resp.status_code if resp is not None else 0
                size = len(resp.content) if resp is not None else 0
                logger.warning(
                    "Failed to record clip from go2rtc for %s: status=%d size=%d",
                    camera_id,
                    status,
                    size,
                )
        except Exception as exc:
            logger.warning("Background clip recording error for %s: %s", camera_id, exc)

    async def _go2rtc_frame(self, camera_id: str) -> bytes | None:
        base = self.config.go2rtc.api_url.rstrip("/")
        cam = self._camera_for(camera_id)
        max_attempts = 2 if (cam and cam.mqtt_id) else 1
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.config.go2rtc.timeout_seconds) as client:
                    resp = await client.get(f"{base}/api/frame.jpeg", params={"src": camera_id})
                if resp.status_code == 200 and resp.content.startswith(b"\xff\xd8"):
                    return resp.content
            except httpx.HTTPError as exc:
                logger.debug(
                    "go2rtc frame unavailable for %s (attempt %d): %s",
                    camera_id,
                    attempt,
                    exc,
                )
            if attempt < max_attempts:
                await asyncio.sleep(1.5)
        return None

    async def _sample_clip_frames(self, clip_path: str, max_frames: int = 5) -> list[bytes]:
        """Best-effort extraction of evenly spaced frames from a saved MP4 clip.

        Returns a list of JPEG-encoded frame bytes. Any failure (missing ffmpeg,
        unreadable clip, etc.) yields an empty list so clip enrichment degrades
        gracefully.
        """
        if not max_frames or max_frames <= 0:
            return []

        import shutil
        import tempfile
        from pathlib import Path

        if shutil.which("ffmpeg") is None:
            return []

        tmpdir = tempfile.mkdtemp(prefix="clip_frames_")
        try:
            # Probe duration to space samples evenly across the clip.
            duration = 10.0
            try:
                probe = await asyncio.create_subprocess_exec(
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    clip_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await probe.communicate()
                if probe.returncode == 0:
                    parsed = float(stdout.decode("utf-8", errors="replace").strip())
                    if parsed > 0:
                        duration = parsed
            except (ValueError, TypeError, OSError):
                duration = 10.0

            interval = max(duration / float(max_frames), 0.1)
            pattern = str(Path(tmpdir) / "frame_%03d.jpg")

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                clip_path,
                "-vf",
                f"fps=1/{interval:.4f}",
                "-vframes",
                str(max_frames),
                "-q:v",
                "2",
                "-threads",
                "1",
                pattern,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                return []

            frames: list[bytes] = []
            for path in sorted(Path(tmpdir).glob("frame_*.jpg"))[:max_frames]:
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if data.startswith(b"\xff\xd8"):
                    frames.append(data)
            return frames
        except Exception:  # pragma: no cover - best effort
            return []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _save_snapshot(self, camera_id: str, kind: str, image: bytes) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        name = f"{camera_id}_{kind}_{stamp}.jpg"
        directory = Path(self.config.storage.base_path).joinpath(*_EVENT_SNAPSHOT_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(image)
        return f"/media/events/{name}"
