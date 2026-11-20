"""FastAPI application factory, lifespan management, and static file mounting."""

import asyncio
import inspect
import logging
import os
import re
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from lookout import __version__
from lookout.adapters.ai_cascade import CascadeVisionAdapter
from lookout.adapters.audio_classifier import YamnetAudioClassifierAdapter
from lookout.adapters.entity_matcher import EntityCatalogMatcher
from lookout.adapters.face_recognition import FaceRecognitionAdapter
from lookout.adapters.go2rtc import Go2RtcAdapter
from lookout.adapters.go2rtc_ptz import Go2RtcPtzAdapter
from lookout.adapters.light_ml import (
    LightMLObjectDetector,
    PetPostureClassifier,
    SpatialZoneEvaluator,
)
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.native_event_poller import NativeEventPoller
from lookout.adapters.native_stream_manager import (
    Go2RtcUnavailableError,
    NativeAwareRecorder,
    NativeStreamManager,
)
from lookout.adapters.ntfy import NtfyDispatcher
from lookout.adapters.ring_native import RingNativeAdapter
from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer
from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.storage_service import StorageService
from lookout.adapters.web.broadcast import SSEBroadcastHub
from lookout.adapters.web.routes import (
    auth_router,
    cameras_router,
    entities_router,
    events_router,
    home_assistant_router,
    native_alias_router,
    native_router,
    providers_router,
    recordings_router,
    settings_router,
    setup_router,
    storage_router,
    system_router,
)
from lookout.adapters.wyze_native import WyzeNativeAdapter, WyzeTutkPtzAdapter
from lookout.config.deep_links import get_deep_link_base
from lookout.config.loader import load_config
from lookout.config.schema import LookoutConfig, RingConfig, StorageConfig, WyzeConfig
from lookout.domain.models import ThreatLevel
from lookout.domain.ptz_service import PtzControlService
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import EventRepositoryPort

logger = logging.getLogger(__name__)


class IngressSimulationMiddleware:
    """ASGI middleware simulating Home Assistant Ingress reverse proxy path routing.

    Intercepts requests starting with `/api/hassio_ingress/{token}`:
    - Injects `X-Ingress-Path: /api/hassio_ingress/{token}`
    - Rewrites the request path to `/{subpath}`
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            match = re.match(r"^/api/hassio_ingress/([^/]+)(/?.*)$", path)
            if match:
                token = match.group(1)
                subpath = match.group(2)
                ingress_path = f"/api/hassio_ingress/{token}"

                rewritten_path = subpath if subpath.startswith("/") else f"/{subpath}"
                scope["path"] = rewritten_path
                if "raw_path" in scope:
                    scope["raw_path"] = rewritten_path.encode("ascii")

                headers = [h for h in scope.get("headers", []) if h[0].lower() != b"x-ingress-path"]
                headers.append((b"x-ingress-path", ingress_path.encode("utf-8")))
                scope["headers"] = headers

        await self.app(scope, receive, send)


def _get_ingress_path(request: Request) -> str:
    """Extract and normalize X-Ingress-Path header."""
    return request.headers.get("x-ingress-path", "").rstrip("/")


def _inject_base_href(html_content: str, ingress_path: str) -> str:
    """Inject or replace base href in HTML content."""
    base_href = f"{ingress_path}/" if ingress_path else "/"
    if re.search(r'<base\s+href="[^"]*"\s*/?>', html_content):
        return re.sub(r'<base\s+href="[^"]*"\s*/?>', f'<base href="{base_href}">', html_content)
    if "<head>" in html_content:
        return html_content.replace("<head>", f'<head>\n  <base href="{base_href}">', 1)
    return html_content


def _native_provider(app: FastAPI, camera_id: str) -> str | None:
    """Return the native provider ('ring'/'wyze') configured for a camera, if any."""
    cfg = getattr(app.state, "config", None)
    for camera in getattr(cfg, "cameras", None) or []:
        if str(getattr(camera, "id", "")) == str(camera_id):
            provider = (getattr(camera, "native_provider", None) or "").lower()
            return provider if provider in ("ring", "wyze") else None
    return None


def _base_camera_id(camera_id: str | None) -> str | None:
    if not camera_id:
        return camera_id
    return camera_id[:-5] if camera_id.endswith("_h264") else camera_id


def _resolve_go2rtc_target(app: FastAPI, camera_id: str | None) -> str:
    """Pick the go2rtc API base URL that holds the stream for ``camera_id``.

    Native-managed cameras live in the embedded go2rtc; everything else uses the
    configured ``go2rtc.api_url``.
    """
    base_id = _base_camera_id(camera_id)
    manager = getattr(app.state, "native_stream_manager", None)
    if base_id and manager is not None and getattr(manager, "api_url", None):
        streams = getattr(manager, "streams", None) or {}
        if camera_id in streams or base_id in streams or _native_provider(app, base_id):
            return str(manager.api_url).rstrip("/")
    cfg = getattr(app.state, "config", None)
    if cfg is not None and hasattr(cfg, "go2rtc") and cfg.go2rtc.api_url:
        return str(cfg.go2rtc.api_url).rstrip("/")
    return "http://localhost:1984"


async def _ensure_native_stream(app: FastAPI, camera_id: str | None) -> bool:
    """Register the native camera's stream in the embedded go2rtc.

    Returns True when the camera is native-managed (and now registered), False
    when it is not native. Raises when the source cannot be resolved/registered.
    """
    base_id = _base_camera_id(camera_id)
    if not base_id:
        return False
    provider = _native_provider(app, base_id)
    manager = getattr(app.state, "native_stream_manager", None)
    if provider is None or manager is None:
        return False
    adapter = getattr(app.state, f"{provider}_native", None)
    if adapter is None:
        return False
    source = adapter._resolve_source(base_id)
    await manager.ensure_stream(base_id, source)
    if provider == "ring" and hasattr(manager, "ensure_h264_transcode"):
        await manager.ensure_h264_transcode(base_id)
    return True


def _get_go2rtc_ws_url(app: FastAPI, query_params: str = "", base: str | None = None) -> str:
    cfg = getattr(app.state, "config", None)
    go2rtc_api = "http://localhost:1984"
    if base:
        go2rtc_api = base.rstrip("/")
    elif cfg is not None and hasattr(cfg, "go2rtc") and cfg.go2rtc.api_url:
        go2rtc_api = cfg.go2rtc.api_url.rstrip("/")

    if go2rtc_api.startswith("https://"):
        base_ws = "wss://" + go2rtc_api[len("https://") :]
    elif go2rtc_api.startswith("http://"):
        base_ws = "ws://" + go2rtc_api[len("http://") :]
    elif go2rtc_api.startswith("ws://") or go2rtc_api.startswith("wss://"):
        base_ws = go2rtc_api
    else:
        base_ws = f"ws://{go2rtc_api}"

    url = f"{base_ws}/api/ws"
    if query_params:
        url = f"{url}?{query_params}"
    return url


_SENSITIVE_QUERY_RE = re.compile(
    r"^(?:uid|enr|mac|did|p2p_id)$|token|password|passwd|pass|key|secret|auth|sig|credential",
    re.IGNORECASE,
)


_SENSITIVE_CLI_RE = re.compile(
    r"(--(?:refresh[-_]?token|token|password|secret|key)(?:=|\s+))(\S+)",
    re.IGNORECASE,
)


def _redact_source(source: str | None) -> str | None:
    """Return the source with URL userinfo, sensitive query values, and CLI tokens redacted."""
    if not source:
        return None
    parts = urlsplit(str(source))
    netloc = parts.netloc.rpartition("@")[2]
    path = _SENSITIVE_CLI_RE.sub(r"\1[redacted]", parts.path)
    query = "&".join(
        f"{k}=[redacted]" if _SENSITIVE_QUERY_RE.search(k) else pair
        for pair in parts.query.split("&")
        if pair
        for k in [pair.partition("=")[0]]
    )
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _source_scheme(source: str | None) -> str | None:
    """Return only the URL scheme of a source."""
    return urlsplit(str(source)).scheme or None if source else None


async def _prepare_live_upstream(
    app: FastAPI, websocket: WebSocket, camera_id: str | None, query_str: str
) -> str | None:
    """Register native streams and build the upstream URL; close the socket on failure."""
    try:
        await _ensure_native_stream(app, camera_id)
    except Exception as exc:
        logger.warning("Native stream registration failed for %s: %s", camera_id, exc)
        with suppress(Exception):
            await websocket.close(code=1011)
        return None
    return _get_go2rtc_ws_url(app, query_str, base=_resolve_go2rtc_target(app, camera_id))


async def _proxy_websocket_to_upstream(websocket: WebSocket, upstream_url: str) -> None:
    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=30.0)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.ws_connect(upstream_url) as upstream_ws,
        ):

            async def client_to_upstream() -> None:
                try:
                    while True:
                        data = await websocket.receive_text()
                        await upstream_ws.send_str(data)
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.debug("Client->Upstream ws proxy closed: %s", e)

            async def upstream_to_client() -> None:
                try:
                    async for msg in upstream_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.debug("Upstream->Client ws proxy closed: %s", e)

            t1 = asyncio.create_task(client_to_upstream())
            t2 = asyncio.create_task(upstream_to_client())

            done, pending = await asyncio.wait(
                [t1, t2],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    except Exception as exc:
        logger.debug("WebRTC WebSocket proxy connection error for %s: %s", upstream_url, exc)
    finally:
        with suppress(Exception):
            await websocket.close()


def _build_notification(
    cfg: LookoutConfig | None,
) -> tuple[NtfyDispatcher | None, ThreatLevel, str]:
    """Create the ntfy dispatcher from config (HA panel links if HA exists, else standalone)."""
    if cfg is None:
        return None, ThreatLevel.LOW, "default"
    target = next(
        (t for t in cfg.notification_targets if t.enabled and t.target_type == "ntfy"), None
    )
    if target is None:
        return None, ThreatLevel.LOW, "default"
    ha_url = (cfg.home_assistant_url or "").rstrip("/")
    public_url = (cfg.public_url or "").rstrip("/")
    # Shared with the HA sync manager so ntfy and discovery links stay in sync.
    deep_base = get_deep_link_base(cfg)
    dispatcher = NtfyDispatcher(
        destination=target.destination,
        deep_link_base_url=deep_base,
        media_base_url=public_url or None,
        use_companion_app=bool(ha_url),
    )
    return dispatcher, target.min_threat_level, "default"


def create_app(
    config: LookoutConfig | None = None,
    repository: EventRepositoryPort | None = None,
    event_service: EventProcessingService | None = None,
    vision_adapter: CascadeVisionAdapter | None = None,
    config_path: Path | str | None = None,
) -> FastAPI:
    """Create and configure a modern Lookout FastAPI web application."""
    resolved_config_path: Path
    if config_path is not None:
        resolved_config_path = Path(config_path)
    elif "LOOKOUT_CONFIG" in os.environ:
        resolved_config_path = Path(os.environ["LOOKOUT_CONFIG"])
    else:
        resolved_config_path = Path("config.yaml")

    is_configured = False
    active_config: LookoutConfig | None = config

    if active_config is not None:
        is_configured = True
    elif resolved_config_path.exists():
        try:
            active_config = load_config(resolved_config_path)
            is_configured = True
        except Exception as exc:
            logger.warning(
                "Failed to load existing configuration at %s: %s", resolved_config_path, exc
            )
            is_configured = False

    active_repo: EventRepositoryPort
    if repository is not None:
        active_repo = repository
    elif (
        active_config is not None
        and getattr(active_config, "storage", None) is not None
        and active_config.storage.database_path
    ):
        try:
            active_repo = SqliteEventRepository(db_path=active_config.storage.database_path)
        except Exception as exc:
            logger.warning(
                "Failed to initialize SqliteEventRepository at %s: %s; "
                "falling back to in-memory repository",
                active_config.storage.database_path,
                exc,
            )
            active_repo = InMemoryEventRepository()
    else:
        active_repo = InMemoryEventRepository()
    active_vision: CascadeVisionAdapter = (
        vision_adapter
        if vision_adapter is not None
        else CascadeVisionAdapter(
            cascade_config=active_config.cascade if active_config else None,
            ollama_config=active_config.ollama if active_config else None,
            opencode_config=active_config.opencode if active_config else None,
            gemini_config=active_config.gemini if active_config else None,
        )
    )

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    media_dir = (
        Path(active_config.storage.base_path)
        if (active_config and getattr(active_config, "storage", None) is not None)
        else Path("/data/media")
        if Path("/data").is_dir()
        else Path("./data")
    )
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        logger.warning("Could not create media directory at %s due to permissions", media_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        log_level_name = os.getenv("LOOKOUT_LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_name, logging.INFO)
        logging.getLogger("lookout").setLevel(log_level)
        if not logging.getLogger().handlers:
            logging.basicConfig(level=log_level, format="%(levelname)s:     %(name)s - %(message)s")
        else:
            logging.getLogger().setLevel(log_level)

        logger.info("Lookout web application starting up (log_level=%s)", log_level_name)
        if app.state.config is not None:
            try:
                SidecarConfigSynchronizer().sync_all(app.state.config)
            except Exception as exc:
                logger.warning("Sidecar configuration synchronization error: %s", exc)
        if hasattr(app.state, "storage_service") and app.state.storage_service is not None:
            app.state.storage_service.start()

        ha_sync_startup = getattr(app.state, "ha_sync", None)
        if ha_sync_startup is not None:
            try:
                _res = ha_sync_startup.sync_all()
                if inspect.isawaitable(_res):
                    await _res
            except Exception as exc:
                logger.warning("Home Assistant sync reconciliation error: %s", exc)

        native_manager = getattr(app.state, "native_stream_manager", None)
        native_cameras = (
            [c for c in active_config.cameras if getattr(c, "native_provider", None)]
            if active_config is not None
            else []
        )
        native_enabled = bool(
            native_cameras
            or (active_config is not None and active_config.wyze.native_ingestion)
            or (active_config is not None and active_config.ring.native_ingestion)
        )
        # Register every enabled camera (generic + native) in embedded go2rtc.
        recorded_ids: set[str] = set()
        if active_config is not None and native_manager is not None:
            wyze_native = getattr(app.state, "wyze_native", None)
            ring_native = getattr(app.state, "ring_native", None)
            for cam in active_config.cameras:
                if not getattr(cam, "enabled", True):
                    continue
                provider = (getattr(cam, "native_provider", None) or "").lower()
                source = getattr(cam, "stream_url", None)
                if provider == "wyze" and wyze_native is not None:
                    try:
                        source = wyze_native._resolve_source(cam.id)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Skipping Wyze source pre-population for %s: %s", cam.id, exc)
                elif provider == "ring" and ring_native is not None:
                    try:
                        source = ring_native._resolve_source(cam.id, snapshot=False)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Skipping Ring source pre-population for %s: %s", cam.id, exc)
                if source:
                    native_manager.streams.setdefault(cam.id, source)
                # resolve recorded
                cr = getattr(cam, "continuous_recording", None)
                global_enabled = getattr(
                    getattr(active_config, "continuous_recording", None), "enabled", True
                )
                if cr is None:
                    cr = global_enabled
                if cr and global_enabled:
                    recorded_ids.add(str(cam.id))
            if recorded_ids and hasattr(native_manager, "set_recorded_cameras"):
                native_manager.set_recorded_cameras(recorded_ids)

        if (native_enabled or recorded_ids) and native_manager is not None:
            ring_native = getattr(app.state, "ring_native", None)
            if ring_native is not None:
                for cam in native_cameras:
                    if (
                        getattr(cam, "native_provider", None) == "ring"
                        and cam.id not in native_manager.streams
                    ):
                        try:
                            native_manager.streams[cam.id] = ring_native._resolve_source(
                                cam.id, snapshot=False
                            )
                        except Exception as exc:
                            logger.debug(
                                "Skipping Ring stream pre-population for %s: %s", cam.id, exc
                            )
            try:
                await native_manager.start()
            except Go2RtcUnavailableError as exc:
                logger.warning("Native streaming unavailable: %s", exc)
            except Exception as exc:
                logger.warning("Failed to start native stream manager: %s", exc)
            if native_enabled:
                native_poller = NativeEventPoller(
                    config=active_config,
                    wyze=getattr(app.state, "wyze_native", None),
                    ring=getattr(app.state, "ring_native", None),
                    ingest=active_event_service.ingest_raw_event,
                    on_event=_broadcast_event,
                    on_clip_ready=_on_clip_ready,
                    stream_manager=native_manager,
                    record_clip=True,
                    clip_duration=10.0,
                    storage_base_path=media_dir,
                )
                app.state.native_event_poller = native_poller
                native_poller.start()

        # Continuous recorder service (start if enabled).
        recorder = getattr(app.state, "continuous_recorder", None)
        if recorder is None:
            try:
                from lookout.adapters.continuous_recorder import ContinuousRecorderService

                recorder = ContinuousRecorderService(
                    config=active_config,
                    cameras=(active_config.cameras if active_config is not None else []),
                    storage_base_path=media_dir,
                    native_rtsp_port=getattr(
                        getattr(active_config, "go2rtc", None), "native_rtsp_port", None
                    ),
                )
                app.state.continuous_recorder = recorder
            except Exception as exc:  # noqa: BLE001
                logger.warning("Continuous recorder unavailable: %s", exc)
                app.state.continuous_recorder = None
                recorder = None
        if recorder is not None and getattr(
            getattr(active_config, "continuous_recording", None), "enabled", True
        ):
            try:
                await recorder.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Continuous recorder failed to start: %s", exc)

        yield
        logger.info("Lookout web application shutting down")
        recorder = getattr(app.state, "continuous_recorder", None)
        if recorder is not None:
            with suppress(Exception):
                await recorder.stop()
        shutdown_poller = getattr(app.state, "native_event_poller", None)
        if shutdown_poller is not None:
            await shutdown_poller.stop()
        ring_native = getattr(app.state, "ring_native", None)
        if ring_native is not None:
            with suppress(Exception):
                await ring_native.close()
        if native_manager is not None:
            with suppress(Exception):
                await native_manager.stop()
        if hasattr(app.state, "storage_service") and app.state.storage_service is not None:
            await app.state.storage_service.stop()
        ha_sync_shutdown = getattr(app.state, "ha_sync", None)
        if ha_sync_shutdown is not None:
            with suppress(Exception):
                await ha_sync_shutdown.aclose()

    app = FastAPI(
        title="Lookout",
        description="Lightweight smart camera intelligence viewer and setup suite",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(IngressSimulationMiddleware)

    notification, notify_threshold, notify_target = _build_notification(active_config)

    active_audio = (
        YamnetAudioClassifierAdapter(config=active_config.audio_classifier)
        if (active_config and active_config.audio_classifier.enabled)
        else YamnetAudioClassifierAdapter()
    )
    active_faces = (
        FaceRecognitionAdapter(
            config=active_config.face_recognition,
            residents=active_config.residents if active_config else None,
        )
        if (active_config and active_config.face_recognition.enabled)
        else FaceRecognitionAdapter()
    )

    active_event_service: EventProcessingService = (
        event_service
        if event_service is not None
        else EventProcessingService(
            repository=active_repo,
            notification=notification,
            vision_inference=active_vision,
            face_recognition=active_faces,
            audio_classifier=active_audio,
            notification_threshold=notify_threshold,
            notification_target=notify_target,
            light_ml_detector=LightMLObjectDetector(),
            pet_classifier=PetPostureClassifier(),
            zone_evaluator=SpatialZoneEvaluator(zones=active_config.zones if active_config else []),
            entity_matcher=EntityCatalogMatcher(
                residents=active_config.residents if active_config else [],
                pets=active_config.pets if active_config else [],
                vehicles=active_config.vehicles if active_config else [],
            )
            if active_config
            else None,
            config=active_config,
        )
    )

    async def _broadcast_event(evt: Any) -> None:
        await app.state.broadcast_hub.broadcast_event(evt)

    async def _on_clip_ready(evt: Any) -> None:
        try:
            frame_samples: list[Any] = []
            if isinstance(getattr(evt, "metadata", None), dict):
                frame_samples = evt.metadata.pop("clip_frame_samples", None) or []
                if frame_samples:
                    evt.metadata["clip_frame_samples_count"] = len(frame_samples)
            if active_repo is not None:
                await active_repo.save(evt)
            await app.state.broadcast_hub.broadcast_event(evt)

            updated = evt
            enrichment_succeeded = False
            if active_event_service is not None:
                try:
                    evt_id = str(getattr(evt, "id", "") or "")
                    enriched = await active_event_service.enrich_event_from_media(
                        evt_id, frame_samples
                    )
                    if enriched is not None:
                        if enriched.media_uri is None and getattr(evt, "media_uri", None):
                            enriched.media_uri = evt.media_uri
                        if isinstance(getattr(enriched, "metadata", None), dict):
                            enriched.metadata.pop("clip_frame_samples", None)
                        updated = enriched
                        enrichment_succeeded = True
                        if active_repo is not None:
                            await active_repo.save(updated)
                        await app.state.broadcast_hub.broadcast_event(updated)
                except Exception as enrich_err:
                    logger.warning(
                        "Second-pass enrichment failed for %s: %s",
                        getattr(evt, "id", "?"),
                        enrich_err,
                    )
                    enrichment_succeeded = False

            if (
                enrichment_succeeded
                and active_event_service is not None
                and active_event_service.notification is not None
                and active_event_service.should_notify(updated, pass_num=2)
            ):
                try:
                    # Dispatch in-place notification update via X-Sequence-ID
                    target = active_event_service.notification_target or "default"
                    message = f"[{updated.threat_level.name}] {updated.summary}"
                    await active_event_service.notification.send_notification(
                        event=updated, target=target, message=message
                    )
                except Exception as notify_err:
                    logger.warning(
                        "Second-pass notification update failed for %s: %s",
                        getattr(updated, "id", "?"),
                        notify_err,
                    )
        except Exception as clip_err:  # pragma: no cover - defensive
            logger.warning("Clip-ready handling failed: %s", clip_err)

    # Attach application state
    app.state.config = active_config
    app.state.config_path = resolved_config_path
    app.state.is_configured = is_configured
    app.state.repository = active_repo
    app.state.event_service = active_event_service
    app.state.vision_adapter = active_vision
    app.state.broadcast_hub = SSEBroadcastHub()
    app.state.custom_entities = {}

    audit_db = ":memory:"
    if (
        active_config
        and getattr(active_config, "storage", None) is not None
        and active_config.storage.database_path
    ):
        audit_db = active_config.storage.database_path
    app.state.audit_logger = AuditLogger(db_path=audit_db)

    # Native ingestion: one embedded go2rtc manager shared by all native adapters.
    native_manager = NativeStreamManager(config=active_config)
    app.state.native_stream_manager = native_manager
    app.state.on_clip_ready = _on_clip_ready
    app.state.continuous_recorder = None
    wyze_native_config = active_config.wyze if active_config is not None else WyzeConfig()
    ring_native_config = active_config.ring if active_config is not None else RingConfig()
    app.state.wyze_native = WyzeNativeAdapter(
        config=wyze_native_config,
        stream_manager=native_manager,
        cameras=(active_config.cameras if active_config is not None else []),
    )
    app.state.ring_native = RingNativeAdapter(
        config=ring_native_config,
        stream_manager=native_manager,
        cameras=(active_config.cameras if active_config is not None else []),
    )

    # Pre-populate embedded go2rtc streams for all enabled cameras (no process start).
    if active_config is not None and native_manager is not None:
        wyze_native = app.state.wyze_native
        ring_native = app.state.ring_native
        recorded_ids: set[str] = set()
        for cam in active_config.cameras:
            if not getattr(cam, "enabled", True):
                continue
            provider = (getattr(cam, "native_provider", None) or "").lower()
            source = getattr(cam, "stream_url", None)
            if provider == "wyze" and wyze_native is not None:
                with suppress(Exception):
                    source = wyze_native._resolve_source(cam.id)
            elif provider == "ring" and ring_native is not None:
                with suppress(Exception):
                    source = ring_native._resolve_source(cam.id, snapshot=False)
            if source:
                native_manager.streams.setdefault(cam.id, source)
            cr = getattr(cam, "continuous_recording", None)
            global_enabled = getattr(
                getattr(active_config, "continuous_recording", None), "enabled", True
            )
            if cr is None:
                cr = global_enabled
            if cr and global_enabled:
                recorded_ids.add(str(cam.id))
        if recorded_ids and hasattr(native_manager, "set_recorded_cameras"):
            native_manager.set_recorded_cameras(recorded_ids)
    # Build per-camera PTZ adapters based on configured protocol
    ptz_adapters: dict[str, Any] = {}
    default_ptz_adapter = Go2RtcPtzAdapter(config=(active_config.go2rtc if active_config else None))
    cam_name_map = {}
    if active_config is not None:
        for cam in active_config.cameras:
            proto = getattr(cam, "ptz_protocol", "none")
            bridge_name = getattr(cam, "ptz_cam_name", None) or cam.id
            cam_name_map[cam.id] = bridge_name
            if proto == "native-wyze" or getattr(cam, "native_provider", None) == "wyze":
                ptz_adapters[cam.id] = WyzeTutkPtzAdapter(
                    config=wyze_native_config,
                    client=app.state.wyze_native,
                    stream_manager=native_manager,
                )
            elif proto in ("go2rtc-wyze", "go2rtc-onvif"):
                ptz_adapters[cam.id] = Go2RtcPtzAdapter(
                    config=(active_config.go2rtc if active_config else None)
                )
    app.state.ptz_service = PtzControlService(
        cameras=(active_config.cameras if active_config else []),
        ptz_adapter=default_ptz_adapter,
        ptz_adapters=ptz_adapters,
        audit_logger=app.state.audit_logger,
        video_recorder=NativeAwareRecorder(
            config=active_config,
            wyze=app.state.wyze_native,
            ring=app.state.ring_native,
            fallback=Go2RtcAdapter(config=(active_config.go2rtc if active_config else None)),
        ),
        config=active_config,
        config_path=resolved_config_path,
    )

    from lookout.adapters.sqlite.members import HouseholdMembersRepository

    db_path = ":memory:"
    if (
        active_config
        and getattr(active_config, "storage", None) is not None
        and active_config.storage.database_path
    ):
        db_path = active_config.storage.database_path
    app.state.members_repo = HouseholdMembersRepository(db_path=db_path)

    storage_cfg = (
        active_config.storage
        if (active_config and getattr(active_config, "storage", None) is not None)
        else StorageConfig()
    )
    storage_svc = StorageService(
        config=storage_cfg,
        repository=active_repo,
        continuous_config=getattr(active_config, "continuous_recording", None),
    )
    app.state.storage_service = storage_svc

    # Home Assistant sync manager.
    from lookout.adapters.home_assistant_sync import HomeAssistantSyncManager

    ha_sync = HomeAssistantSyncManager(
        config=active_config,
        storage_base_path=str(media_dir),
        cameras=(active_config.cameras if active_config is not None else []),
    )
    app.state.ha_sync = ha_sync

    # Continuous recorder service (instantiated eagerly so status/endpoints work).
    try:
        from lookout.adapters.continuous_recorder import ContinuousRecorderService

        recorder = ContinuousRecorderService(
            config=active_config,
            cameras=(active_config.cameras if active_config is not None else []),
            storage_base_path=media_dir,
            native_rtsp_port=getattr(
                getattr(active_config, "go2rtc", None), "native_rtsp_port", None
            ),
        )
        app.state.continuous_recorder = recorder
    except Exception as exc:  # noqa: BLE001
        logger.warning("Continuous recorder unavailable: %s", exc)
        app.state.continuous_recorder = None

    # Mount REST API routers
    app.include_router(events_router, prefix="/api")
    app.include_router(native_router, prefix="/api")
    app.include_router(native_alias_router, prefix="/api")
    app.include_router(cameras_router, prefix="/api")
    app.include_router(entities_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(home_assistant_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(storage_router, prefix="/api")
    app.include_router(recordings_router, prefix="/api")
    app.include_router(system_router, prefix="/api")

    # Static assets with no-cache revalidation
    class NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Any) -> Response:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        """Return 204 No Content for favicon requests to prevent console 404 noise."""
        return Response(status_code=204)

    # Transparent multi-tier media stream resolver
    @app.api_route("/media/{file_path:path}", methods=["GET", "HEAD"])
    async def serve_media(file_path: str, request: Request) -> Response:
        """Serve media transparently across local base_path and network archive_path."""
        clean_path = Path(file_path)
        if clean_path.is_absolute() or ".." in clean_path.parts:
            return JSONResponse(status_code=400, content={"error": "Invalid file path"})

        # 1. Local hot tier
        candidate_paths: list[Path] = [
            media_dir / clean_path,
            media_dir / "media" / clean_path,
            media_dir / "events" / clean_path,
            media_dir / "events" / clean_path.name,
            media_dir / "recordings" / clean_path,
            media_dir / "recordings" / clean_path.name,
        ]

        # 2. Warm archive tier
        cfg = getattr(app.state, "config", None)
        if cfg and getattr(cfg, "storage", None) is not None and cfg.storage.archive_path:
            archive_base = Path(cfg.storage.archive_path).resolve()
            candidate_paths.extend(
                [
                    archive_base / clean_path,
                    archive_base / "media" / clean_path,
                    archive_base / "events" / clean_path,
                    archive_base / "events" / clean_path.name,
                ]
            )

        # 3. Container mount fallbacks
        candidate_paths.extend(
            [
                media_dir / "snapshots" / "events" / clean_path.name,
                Path("/tmp/lookout_snapshots") / clean_path.name,
                Path("/media") / clean_path,
                Path("/media/events") / clean_path,
                Path("/media/events") / clean_path.name,
                Path("/snapshots") / clean_path,
                Path("/snapshots/events") / clean_path.name,
                Path("/snapshots") / clean_path.name,
            ]
        )

        for candidate in candidate_paths:
            try:
                resolved = candidate.resolve()
                if resolved.is_file():
                    return FileResponse(resolved, headers={"Accept-Ranges": "bytes"})
            except (OSError, PermissionError):
                continue

        # If .jpg requested and not found, extract thumbnail from matching .mp4 if available
        if clean_path.suffix.lower() in (".jpg", ".jpeg"):
            cache_dirs = [
                media_dir / "snapshots" / "events",
                Path("/tmp/lookout_snapshots"),
            ]
            for candidate in candidate_paths:
                mp4_cand = candidate.with_suffix(".mp4")
                try:
                    if mp4_cand.resolve().is_file():
                        import subprocess

                        target_jpg: Path | None = None
                        for c_dir in cache_dirs:
                            try:
                                c_dir.mkdir(parents=True, exist_ok=True)
                                target_candidate = c_dir / f"{clean_path.stem}.jpg"
                                if target_candidate.is_file():
                                    return FileResponse(target_candidate, media_type="image/jpeg")
                                target_jpg = target_candidate
                                break
                            except Exception:
                                continue

                        if target_jpg is not None:
                            await asyncio.to_thread(
                                subprocess.run,
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-ss",
                                    "00:00:00.500",
                                    "-i",
                                    str(mp4_cand),
                                    "-frames:v",
                                    "1",
                                    "-q:v",
                                    "2",
                                    str(target_jpg),
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=6.0,
                            )
                            if target_jpg.is_file():
                                return FileResponse(target_jpg, media_type="image/jpeg")
                except Exception:
                    continue

        return JSONResponse(
            status_code=404,
            content={
                "error": "Media file not found or has been purged by retention policy",
                "path": file_path,
            },
        )

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint for container probes and ingress verification."""
        return {
            "status": "ok",
            "configured": app.state.is_configured,
            "version": __version__,
        }

    @app.post("/api/v1/events/trigger")
    @app.post("/api/events/trigger")
    async def trigger_event(request: Request) -> dict[str, Any]:
        """Ingest event trigger request from Home Assistant or external webhook."""
        try:
            payload = await request.json()
        except Exception as exc:
            logger.warning("Failed to parse trigger event JSON payload: %s", exc)
            payload = {}
        if not isinstance(payload, dict):
            logger.warning(
                "Trigger event payload must be a JSON dictionary; received %s",
                type(payload).__name__,
            )
            payload = {"data": payload}
        camera_id = str(
            payload.get("camera_id")
            or payload.get("camera")
            or payload.get("cameraId")
            or "default"
        )
        svc: EventProcessingService = app.state.event_service
        event = await svc.ingest_raw_event(camera_id, payload)
        hub: SSEBroadcastHub = app.state.broadcast_hub
        await hub.broadcast_event(event)
        return {"status": "ok", "event_id": event.id, "id": event.id}

    # WebRTC live streaming WebSocket and HTTP signaling proxies to go2rtc
    @app.websocket("/api/ws")
    async def websocket_proxy(websocket: WebSocket) -> None:
        """Proxy client WebRTC WebSocket connections directly to go2rtc."""
        await websocket.accept()
        query_str = str(websocket.query_params)
        upstream_url = await _prepare_live_upstream(
            app, websocket, websocket.query_params.get("src"), query_str
        )
        if upstream_url is None:
            return
        await _proxy_websocket_to_upstream(websocket, upstream_url)

    @app.websocket("/api/webrtc/{camera_id}/ws")
    async def websocket_camera_proxy(websocket: WebSocket, camera_id: str) -> None:
        """Proxy camera-specific WebRTC WebSocket connections to go2rtc."""
        await websocket.accept()
        query_parts = [f"src={camera_id}"]
        if str(websocket.query_params):
            query_parts.append(str(websocket.query_params))
        query_str = "&".join(query_parts)
        upstream_url = await _prepare_live_upstream(app, websocket, camera_id, query_str)
        if upstream_url is None:
            return
        await _proxy_websocket_to_upstream(websocket, upstream_url)

    @app.get("/api/cameras/{camera_id}/live/diagnostics")
    async def live_diagnostics(camera_id: str) -> dict[str, Any]:
        """Report which go2rtc holds the stream and which codecs it serves."""
        manager = getattr(app.state, "native_stream_manager", None)
        source = (getattr(manager, "streams", None) or {}).get(camera_id)
        registered = False
        codecs: list[str] = []
        if manager is not None and getattr(manager, "api_url", None) and source:
            with suppress(Exception):
                registered = bool(await manager._stream_exists(camera_id))
            with suppress(Exception):
                codecs = list(await manager.stream_codecs(camera_id))
        return {
            "stream_registered": registered,
            "source_scheme": _source_scheme(source),
            "source": _redact_source(source),
            "codecs": codecs,
            "h264": any("H264" in c.upper() for c in codecs),
            "go2rtc_target": _resolve_go2rtc_target(app, camera_id),
        }

    @app.post("/api/webrtc")
    async def proxy_webrtc_post(request: Request) -> Response:
        """Proxy WebRTC HTTP signaling to go2rtc."""
        query_str = str(request.url.query)
        src = request.query_params.get("src")
        try:
            await _ensure_native_stream(app, src)
        except Exception as exc:
            logger.warning("Native stream registration failed for %s: %s", src, exc)
            return JSONResponse(status_code=502, content={"error": "stream unavailable"})
        go2rtc_api = _resolve_go2rtc_target(app, src)
        upstream_url = f"{go2rtc_api}/api/webrtc"
        if query_str:
            upstream_url = f"{upstream_url}?{query_str}"

        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(upstream_url, content=body, headers=headers)
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type"),
                )
        except Exception as exc:
            logger.debug("Error proxying /api/webrtc POST to go2rtc: %s", exc)
            return JSONResponse(status_code=502, content={"error": "go2rtc unavailable"})

    # Root viewer entrypoint with setup redirection guardrail
    @app.get("/", include_in_schema=False)
    async def root_view(request: Request) -> Response:
        ingress_path = _get_ingress_path(request)
        if not app.state.is_configured:
            setup_redirect = f"{ingress_path}/setup" if ingress_path else "/setup"
            return RedirectResponse(url=setup_redirect, status_code=307)

        index_file = static_dir / "index.html"
        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            return HTMLResponse(_inject_base_href(content, ingress_path))
        return HTMLResponse("<!DOCTYPE html><html><body><h1>Lookout Web Viewer</h1></body></html>")

    # Deep-linked viewer entrypoints (NTFY action buttons & Home Assistant links)
    @app.get("/events/{event_id}", include_in_schema=False)
    @app.get("/cameras/{camera_id}/live", include_in_schema=False)
    async def deep_link_viewer(request: Request) -> Response:
        """Serve the viewer SPA for deep-linked event or camera views."""
        return await root_view(request)

    # Setup wizard entrypoint
    @app.get("/setup", include_in_schema=False)
    async def setup_view(request: Request) -> Response:
        ingress_path = _get_ingress_path(request)
        setup_file = static_dir / "setup.html"
        if setup_file.exists():
            content = setup_file.read_text(encoding="utf-8")
            return HTMLResponse(_inject_base_href(content, ingress_path))
        return HTMLResponse(
            "<!DOCTYPE html><html><body><h1>Lookout Setup Wizard</h1></body></html>"
        )

    return app
