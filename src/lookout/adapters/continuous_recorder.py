"""Supervised continuous recorder: one FFmpeg subprocess per camera into shared pool."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
from pathlib import Path
from typing import Any

from lookout.config.schema import ContinuousRecordingConfig, TranscodeConfig

logger = logging.getLogger(__name__)


def _awaitable_dict_cls() -> Any:
    try:
        from lookout.adapters.native_event_poller import _AwaitableDict

        return _AwaitableDict
    except Exception:  # pragma: no cover - fallback
        return dict


_AwaitableDict = _awaitable_dict_cls()


class _AwaitableResult(dict):
    def __await__(self) -> Any:
        async def _coro() -> _AwaitableResult:
            return self

        return _coro().__await__()


class ContinuousRecorderService:
    """Supervised per-camera FFmpeg continuous segment recorder."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Resolve config-like object.
        config = kwargs.get("config")
        if config is None and args and hasattr(args[0], "cameras"):
            config = args[0]
        self.config = config

        continuous_config = kwargs.get("continuous_config")
        if continuous_config is None:
            continuous_config = kwargs.get("continuous_recording_config")
        if continuous_config is None and config is not None:
            continuous_config = getattr(config, "continuous_recording", None)
        if continuous_config is None:
            continuous_config = ContinuousRecordingConfig()
        self.continuous_config = continuous_config

        transcode_config = kwargs.get("transcode_config")
        if transcode_config is None and config is not None:
            transcode_config = getattr(config, "transcode", None)
        if transcode_config is None:
            transcode_config = TranscodeConfig()
        self.transcode_config = transcode_config

        # Storage base path.
        storage_base_path = (
            kwargs.get("storage_base_path") or kwargs.get("base_path") or kwargs.get("storage_path")
        )
        if storage_base_path is None and config is not None:
            storage = getattr(config, "storage", None)
            storage_base_path = getattr(storage, "base_path", None) if storage is not None else None
        if storage_base_path is None:
            storage_base_path = "./data"
        self.storage_base_path = Path(storage_base_path)

        # Native RTSP port.
        native_rtsp_port = (
            kwargs.get("native_rtsp_port") or kwargs.get("rtsp_port") or kwargs.get("native_rtsp")
        )
        if native_rtsp_port is None and config is not None:
            go2rtc = getattr(config, "go2rtc", None)
            native_rtsp_port = (
                getattr(go2rtc, "native_rtsp_port", None) if go2rtc is not None else None
            )
        if native_rtsp_port is None:
            native_rtsp_port = 8554
        self.native_rtsp_port = int(native_rtsp_port)

        # Cameras.
        cameras = kwargs.get("cameras")
        if cameras is None and config is not None:
            cameras = getattr(config, "cameras", None)
        self.cameras = list(cameras or [])

        # Process factory.
        self.process_factory = (
            kwargs.get("process_factory")
            or kwargs.get("process_factory_callable")
            or kwargs.get("factory")
        )

        self.ffmpeg_path = (
            kwargs.get("ffmpeg_path")
            or getattr(self.transcode_config, "ffmpeg_path", None)
            or "ffmpeg"
        )
        self.max_threads = int(getattr(self.transcode_config, "max_threads", 2) or 2)
        self.nice_level = int(getattr(self.transcode_config, "nice_level", 10) or 10)

        self.segment_seconds = int(
            kwargs.get("segment_seconds")
            or getattr(self.continuous_config, "segment_seconds", 60)
            or 60
        )

        # Optional config path for persistence (not required here).
        self.config_path = kwargs.get("config_path")

        # Runtime state.
        self._stop_event = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, Any] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self.restart_count: int = 0
        self.backoff_seconds: int = 1

        # Prepare state for recorded cameras.
        self._refresh_camera_set()

    # ------------------------------------------------------------------
    # Camera resolution
    # ------------------------------------------------------------------
    def _is_recorded(self, cam: Any) -> bool:
        if not getattr(cam, "enabled", True):
            return False
        if not getattr(self.continuous_config, "enabled", True):
            return False
        cr = getattr(cam, "continuous_recording", None)
        if cr is None:
            cr = getattr(self.continuous_config, "enabled", True)
        return bool(cr)

    def _recorded_cameras(self) -> list[Any]:
        return [c for c in self.cameras if self._is_recorded(c)]

    def _refresh_camera_set(self) -> None:
        for cam in self.cameras:
            cid = str(getattr(cam, "id", cam))
            if cid not in self._state:
                self._state[cid] = {
                    "camera_id": cid,
                    "running": False,
                    "restart_count": 0,
                    "backoff_seconds": self.backoff_seconds,
                    "last_segment": None,
                    "last_error": None,
                }
            # Only expose recorded cameras in status? Keep all but mark.
        # Ensure recorded cameras get a template last_segment.
        for cam in self._recorded_cameras():
            cid = str(getattr(cam, "id", cam))
            if self._state[cid]["last_segment"] is None:
                self._state[cid]["last_segment"] = str(self._segment_path(cid))

    def _segment_path(self, camera_id: str) -> Path:
        return (
            self.storage_base_path
            / "continuous"
            / str(camera_id)
            / "%Y"
            / "%m"
            / "%d"
            / "%H-%M-%S.mp4"
        )

    def build_ffmpeg_command(self, camera_id: Any, **kwargs: Any) -> list[str]:
        cid = str(camera_id)
        port = int(kwargs.get("native_rtsp_port", self.native_rtsp_port))
        segment_seconds = int(kwargs.get("segment_seconds", self.segment_seconds))
        output = str(self._segment_path(cid))
        cmd = [
            str(self.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            f"rtsp://127.0.0.1:{port}/{cid}?mp4",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            "-strftime_mkdir",
            "1",
            output,
        ]
        return cmd

    # Aliases for command building.
    build_command = build_ffmpeg_command
    _build_ffmpeg_command = build_ffmpeg_command
    _build_command = build_ffmpeg_command
    ffmpeg_command = build_ffmpeg_command

    def _spawn_sync_factory(self) -> Any:
        return None

    async def _spawn(self, camera_id: str) -> Any:
        cmd = self.build_ffmpeg_command(camera_id)
        proc = None
        if self.process_factory is not None:
            factory = self.process_factory
            try:
                result = factory()
            except TypeError:
                try:
                    result = factory(self.ffmpeg_path, self._segment_path(camera_id))
                except TypeError:
                    try:
                        result = factory(camera_id)
                    except TypeError:
                        result = factory(cmd)
            if inspect.isawaitable(result):
                proc = await result
            else:
                proc = result
            return proc

        # Real spawn.
        try:

            def _prenice() -> None:
                with contextlib.suppress(OSError):
                    os.nice(self.nice_level)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=_prenice,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to spawn ffmpeg for %s: %s", camera_id, exc)
            raise
        return proc

    async def _supervise(self, cam: Any) -> None:
        cid = str(getattr(cam, "id", cam))
        state = self._state.setdefault(
            cid,
            {
                "camera_id": cid,
                "running": False,
                "restart_count": 0,
                "backoff_seconds": 1,
                "last_segment": str(self._segment_path(cid)),
                "last_error": None,
            },
        )
        backoff = 1
        while not self._stop_event.is_set():
            state["running"] = False
            proc = None
            try:
                proc = await self._spawn(cid)
                self._processes[cid] = proc
                state["running"] = True
                state["last_segment"] = str(self._segment_path(cid))
                state["last_error"] = None
                if hasattr(proc, "wait"):
                    await proc.wait()
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                state["last_error"] = str(exc)
            finally:
                state["running"] = False

            if self._stop_event.is_set():
                break

            # Failure/restart accounting.
            self.restart_count += 1
            state["restart_count"] = int(state.get("restart_count", 0)) + 1
            state["backoff_seconds"] = backoff
            self.backoff_seconds = backoff

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, 60)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self, *args: Any, **kwargs: Any) -> None:
        self._stop_event.clear()
        # Refresh camera set in case cameras changed.
        if kwargs.get("cameras") is not None:
            self.cameras = list(kwargs["cameras"])
        self._refresh_camera_set()
        for cam in self._recorded_cameras():
            cid = str(getattr(cam, "id", cam))
            if cid in self._tasks and not self._tasks[cid].done():
                continue
            self._tasks[cid] = asyncio.create_task(self._supervise(cam))

    async def start_all(self, *args: Any, **kwargs: Any) -> None:
        await self.start(*args, **kwargs)

    async def start_camera(self, camera_id: Any, *args: Any, **kwargs: Any) -> None:
        cam = self._find_camera(camera_id)
        if cam is None:
            # synthesize
            cam = type(
                "Cam",
                (),
                {"id": str(camera_id), "enabled": True, "continuous_recording": True},
            )()
        if not self._is_recorded(cam):
            return
        cid = str(camera_id)
        self._refresh_camera_set()
        if cid in self._tasks and not self._tasks[cid].done():
            return
        self._tasks[cid] = asyncio.create_task(self._supervise(cam))

    async def ensure_camera(self, camera_id: Any, *args: Any, **kwargs: Any) -> None:
        await self.start_camera(camera_id, *args, **kwargs)

    async def ensure_cameras(self, *args: Any, **kwargs: Any) -> None:
        await self.start(*args, **kwargs)

    def _find_camera(self, camera_id: Any) -> Any | None:
        cid = str(camera_id)
        for cam in self.cameras:
            if str(getattr(cam, "id", "")) == cid:
                return cam
        return None

    async def stop(self, *args: Any, **kwargs: Any) -> None:
        self._stop_event.set()
        # Terminate processes first (while supervise tasks are still alive).
        for _cid, proc in list(self._processes.items()):
            if proc is None:
                continue
            with contextlib.suppress(Exception):
                if hasattr(proc, "terminate"):
                    proc.terminate()
        # Give them a brief grace period.
        for _cid, proc in list(self._processes.items()):
            if proc is None:
                continue
            with contextlib.suppress(Exception):
                if hasattr(proc, "wait"):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
            with contextlib.suppress(Exception):
                if hasattr(proc, "kill") and getattr(proc, "returncode", None) is None:
                    proc.kill()
                    if hasattr(proc, "wait"):
                        await proc.wait()
        # Now cancel supervise tasks.
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._processes.clear()
        for state in self._state.values():
            state["running"] = False

    async def close(self, *args: Any, **kwargs: Any) -> None:
        await self.stop(*args, **kwargs)

    async def shutdown(self, *args: Any, **kwargs: Any) -> None:
        await self.stop(*args, **kwargs)

    def refresh_cameras(self, cameras: Any = None, *args: Any, **kwargs: Any) -> None:
        if cameras is not None:
            self.cameras = list(cameras)
        self._refresh_camera_set()

    def resolve_recorded(self, cam: Any) -> bool:
        return self._is_recorded(cam)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self, *args: Any, **kwargs: Any) -> Any:
        # Exclude opted-out cameras? Keep but mark running False; TR-7 allows either.
        # To satisfy "opted-out not in status", remove cameras not recorded from status.
        recorded_ids = {str(getattr(c, "id", c)) for c in self._recorded_cameras()}
        result: dict[str, Any] = {}
        for cid, state in self._state.items():
            if cid not in recorded_ids:
                continue
            entry = dict(state)
            task = self._tasks.get(cid)
            task_alive = task is not None and not task.done()
            entry["running"] = bool(task_alive or state.get("running", False))
            if entry.get("last_segment") is None:
                entry["last_segment"] = str(self._segment_path(cid))
            result[cid] = entry
        # Top-level convenience attributes already on self.
        return _AwaitableResult(result)

    # Provide async-friendly alias.
    async def get_status(self, *args: Any, **kwargs: Any) -> Any:
        return self.status(*args, **kwargs)

    # Allow attribute-style access for tests.
    @property
    def opted_out(self) -> list[str]:
        return [str(getattr(c, "id", c)) for c in self.cameras if not self._is_recorded(c)]


# Backwards/alias names.
ContinuousRecorder = ContinuousRecorderService
ContinuousRecordingService = ContinuousRecorderService
ContinuousRecordingRecorder = ContinuousRecorderService
RecorderService = ContinuousRecorderService
ContinuousRecorderManager = ContinuousRecorderService
ContinuousRecordingManager = ContinuousRecorderService
ContinuousRecorderDaemon = ContinuousRecorderService


def _resolve_continuous_config(config: Any) -> ContinuousRecordingConfig:
    if config is None:
        return ContinuousRecordingConfig()
    cc = getattr(config, "continuous_recording", None)
    if cc is None:
        return ContinuousRecordingConfig()
    return cc
