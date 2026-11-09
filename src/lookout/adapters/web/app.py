"""FastAPI application factory, lifespan management, and static file mounting."""

import asyncio
import logging
import os
import re
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from lookout import __version__
from lookout.adapters.ai_cascade import CascadeVisionAdapter
from lookout.adapters.entity_matcher import EntityCatalogMatcher
from lookout.adapters.light_ml import (
    LightMLObjectDetector,
    PetPostureClassifier,
    SpatialZoneEvaluator,
)
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.sidecar_sync import SidecarConfigSynchronizer
from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.storage_service import StorageService
from lookout.adapters.web.broadcast import SSEBroadcastHub
from lookout.adapters.web.routes import (
    auth_router,
    cameras_router,
    entities_router,
    events_router,
    providers_router,
    settings_router,
    setup_router,
    storage_router,
    system_router,
)
from lookout.config.loader import load_config
from lookout.config.schema import LookoutConfig, StorageConfig
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


def _get_go2rtc_ws_url(app: FastAPI, query_params: str = "") -> str:
    cfg = getattr(app.state, "config", None)
    go2rtc_api = "http://localhost:1984"
    if cfg is not None and hasattr(cfg, "go2rtc") and cfg.go2rtc.api_url:
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
        and hasattr(active_config, "storage")
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
        else CascadeVisionAdapter(cascade_config=active_config.cascade if active_config else None)
    )

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    media_dir = (
        Path(active_config.storage.base_path)
        if (active_config and hasattr(active_config, "storage"))
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
        logger.info("Lookout web application starting up")
        if app.state.config is not None:
            try:
                SidecarConfigSynchronizer().sync_all(app.state.config)
            except Exception as exc:
                logger.warning("Sidecar configuration synchronization error: %s", exc)
        if hasattr(app.state, "storage_service") and app.state.storage_service is not None:
            app.state.storage_service.start()
        yield
        logger.info("Lookout web application shutting down")
        if hasattr(app.state, "storage_service") and app.state.storage_service is not None:
            await app.state.storage_service.stop()

    app = FastAPI(
        title="Lookout",
        description="Lightweight smart camera intelligence viewer and setup suite",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(IngressSimulationMiddleware)

    active_event_service: EventProcessingService = (
        event_service
        if event_service is not None
        else EventProcessingService(
            repository=active_repo,
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
        )
    )

    # Attach application state
    app.state.config = active_config
    app.state.config_path = resolved_config_path
    app.state.is_configured = is_configured
    app.state.repository = active_repo
    app.state.event_service = active_event_service
    app.state.vision_adapter = active_vision
    app.state.broadcast_hub = SSEBroadcastHub()
    app.state.custom_entities = {}

    from lookout.adapters.sqlite.members import HouseholdMembersRepository

    db_path = ":memory:"
    if active_config and hasattr(active_config, "storage") and active_config.storage.database_path:
        db_path = active_config.storage.database_path
    app.state.members_repo = HouseholdMembersRepository(db_path=db_path)

    storage_cfg = (
        active_config.storage
        if (active_config and hasattr(active_config, "storage"))
        else StorageConfig()
    )
    storage_svc = StorageService(config=storage_cfg, repository=active_repo)
    app.state.storage_service = storage_svc

    # Mount REST API routers
    app.include_router(events_router, prefix="/api")
    app.include_router(cameras_router, prefix="/api")
    app.include_router(entities_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(storage_router, prefix="/api")
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
        ]

        # 2. Warm archive tier
        cfg = getattr(app.state, "config", None)
        if cfg and hasattr(cfg, "storage") and cfg.storage.archive_path:
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
                            subprocess.run(
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
        upstream_url = _get_go2rtc_ws_url(app, query_str)
        await _proxy_websocket_to_upstream(websocket, upstream_url)

    @app.websocket("/api/webrtc/{camera_id}/ws")
    async def websocket_camera_proxy(websocket: WebSocket, camera_id: str) -> None:
        """Proxy camera-specific WebRTC WebSocket connections to go2rtc."""
        await websocket.accept()
        query_parts = [f"src={camera_id}"]
        if str(websocket.query_params):
            query_parts.append(str(websocket.query_params))
        query_str = "&".join(query_parts)
        upstream_url = _get_go2rtc_ws_url(app, query_str)
        await _proxy_websocket_to_upstream(websocket, upstream_url)

    @app.post("/api/webrtc")
    async def proxy_webrtc_post(request: Request) -> Response:
        """Proxy WebRTC HTTP signaling to go2rtc."""
        cfg = getattr(app.state, "config", None)
        go2rtc_api = "http://localhost:1984"
        if cfg is not None and hasattr(cfg, "go2rtc") and cfg.go2rtc.api_url:
            go2rtc_api = cfg.go2rtc.api_url.rstrip("/")

        query_str = str(request.url.query)
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
