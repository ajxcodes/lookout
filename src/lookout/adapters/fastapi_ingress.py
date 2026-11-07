"""FastAPI ingress adapter exposing HTTP trigger endpoint."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lookout.adapters.memory.repository import InMemoryEventRepository
from lookout.domain.service import EventProcessingService
from lookout.ports.ingress import EventIngressPort

logger = logging.getLogger(__name__)


def create_app(
    service: EventProcessingService | None = None,
    repository: Any = None,
    notification: Any = None,
    **kwargs: Any,
) -> FastAPI:
    if service is None:
        repo = repository
        if repo is None:
            repo = InMemoryEventRepository()
        service = EventProcessingService(
            repository=repo,
            notification=notification,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in {
                    "video_recorder",
                    "vision_inference",
                    "audio_classifier",
                    "face_recognition",
                    "notification_threshold",
                    "notification_target",
                }
            },
        )
    app = FastAPI(title="Lookout Ingress")
    app.state.service = service

    @app.post("/api/v1/events/trigger")
    async def trigger_event(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        camera_id = (
            payload.get("camera_id")
            or payload.get("camera")
            or payload.get("cameraId")
            or kwargs.get("camera_id")
            or "default"
        )
        camera_id = str(camera_id)
        event = await service.ingest_raw_event(camera_id, payload)
        return JSONResponse(
            status_code=200,
            content={"event_id": event.id, "id": event.id, "status": "ok"},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


class FastAPIIngressAdapter(EventIngressPort):
    """Adapter wrapping a FastAPI app and delegating to EventProcessingService."""

    def __init__(
        self,
        service: EventProcessingService | None = None,
        repository: Any = None,
        app: FastAPI | None = None,
        **kwargs: Any,
    ) -> None:
        if service is None:
            repo = repository
            if repo is None:
                repo = InMemoryEventRepository()
            service = EventProcessingService(
                repository=repo,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k
                    in {
                        "notification",
                        "video_recorder",
                        "vision_inference",
                        "audio_classifier",
                        "face_recognition",
                        "notification_threshold",
                        "notification_target",
                    }
                },
            )
        self.service = service
        self.app = app if app is not None else create_app(service=service)

    async def ingest_raw_event(self, camera_id: str, payload: Any) -> Any:
        return await self.service.ingest_raw_event(camera_id, payload)

    async def process_event(self, event: Any) -> Any:
        return await self.service.process_event(event)


FastAPIAdapter = FastAPIIngressAdapter
FastAPIIngress = FastAPIIngressAdapter
IngressFastAPIAdapter = FastAPIIngressAdapter
