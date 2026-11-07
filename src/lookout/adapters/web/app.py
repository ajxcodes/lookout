"""FastAPI application factory, lifespan management, and static file mounting."""

import logging
import os
import re
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from lookout import __version__
from lookout.adapters.ai_cascade import CascadeVisionAdapter
from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.broadcast import SSEBroadcastHub
from lookout.adapters.web.routes import (
    cameras_router,
    events_router,
    settings_router,
    setup_router,
)
from lookout.config.loader import load_config
from lookout.config.schema import LookoutConfig
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

    active_repo: EventRepositoryPort = (
        repository if repository is not None else InMemoryEventRepository()
    )
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
        yield
        logger.info("Lookout web application shutting down")

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
        else EventProcessingService(repository=active_repo)
    )

    # Attach application state
    app.state.config = active_config
    app.state.config_path = resolved_config_path
    app.state.is_configured = is_configured
    app.state.repository = active_repo
    app.state.event_service = active_event_service
    app.state.vision_adapter = active_vision
    app.state.broadcast_hub = SSEBroadcastHub()

    # Mount REST API routers
    app.include_router(events_router, prefix="/api")
    app.include_router(cameras_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")

    # Static assets and media files
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

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
