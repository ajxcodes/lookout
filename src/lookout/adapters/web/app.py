"""FastAPI application factory, lifespan management, and static file mounting."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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
        else Path("./data")
    )
    media_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("Lookout web application starting up")
        yield
        logger.info("Lookout web application shutting down")

    app = FastAPI(
        title="Lookout",
        description="Lightweight smart camera intelligence viewer and setup suite",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach application state
    app.state.config = active_config
    app.state.config_path = resolved_config_path
    app.state.is_configured = is_configured
    app.state.repository = active_repo
    app.state.event_service = event_service
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

    # Root viewer entrypoint with setup redirection guardrail
    @app.get("/", include_in_schema=False)
    async def root_view(request: Request) -> Response:
        if not app.state.is_configured:
            return RedirectResponse(url="/setup", status_code=307)

        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<!DOCTYPE html><html><body><h1>Lookout Web Viewer</h1></body></html>")

    # Setup wizard entrypoint
    @app.get("/setup", include_in_schema=False)
    async def setup_view(request: Request) -> Response:
        setup_file = static_dir / "setup.html"
        if setup_file.exists():
            return FileResponse(setup_file)
        return HTMLResponse(
            "<!DOCTYPE html><html><body><h1>Lookout Setup Wizard</h1></body></html>"
        )

    return app
