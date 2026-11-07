# Lookout Architecture Guide

This document provides a comprehensive overview of Lookout's system architecture, design principles, module boundaries, and guidelines for extending the platform with custom ports and adapters.

---

## 1. Architectural Philosophy

Lookout adheres strictly to **Hexagonal Architecture** (also known as Ports and Adapters). The core objective is decoupling domain business logic from infrastructure, storage backends, external APIs, and presentation layers.

```
       +-------------------------------------------------------+
       |                  Presentation / Ingress               |
       |  FastAPI REST API  |  Web Viewer SPA  |  SSE Hub      |
       +-------------------------------------------------------+
                                   |
                                   v  (uses Ingress Port)
       +-------------------------------------------------------+
       |                    Lookout Domain                     |
       |                                                       |
       |   Models: CameraEvent, ThreatLevel, CascadeResult     |
       |   Services: EventProcessingService                    |
       +-------------------------------------------------------+
                                   |
                                   v  (calls Egress Port)
       +-------------------------------------------------------+
       |                   Infrastructure / Egress             |
       |  SQLite WAL Repo  |  FFmpeg  |  AI Cascade  |  HA Bus |
       +-------------------------------------------------------+
```

### Core Invariants & Boundaries
1. **Zero External I/O in Domain**:
   - `lookout.domain` must NEVER import `fastapi`, `starlette`, `httpx`, `sqlite3`, `subprocess`, `requests`, or any adapter module.
   - All external communication occurs strictly through protocols defined in `lookout.ports`.
2. **Ports are Runtime-Checkable Protocols**:
   - Every port in `lookout.ports.ingress` and `lookout.ports.egress` is a `@runtime_checkable typing.Protocol`.
   - Adapters implement these protocols implicitly via structural typing.
3. **No Sibling Adapter Cross-Imports**:
   - An adapter in `lookout.adapters` must not directly import another sibling adapter (e.g. `adapters.sqlite` must never import `adapters.web`).
   - Adapters interact solely via domain services or through dependency injection.

---

## 2. Directory Layout & Layer Responsibilities

```
src/lookout/
├── domain/                    # Pure business logic and domain entities
│   ├── models.py              # CameraEvent, ThreatLevel, EventCategory, etc.
│   └── service.py             # EventProcessingService orchestration
├── ports/                     # Protocol boundary contracts
│   ├── ingress.py             # EventIngressPort
│   └── egress.py              # EventRepositoryPort, VisionInferencePort, etc.
├── adapters/                  # Concrete infrastructure implementations
│   ├── sqlite/                # SQLite WAL repository and FTS5 search
│   ├── ai_cascade.py          # Multi-tier local/remote inference cascade
│   ├── ffmpeg.py              # Video transcoding and snapshot extraction
│   ├── go2rtc.py              # go2rtc stream hub integration
│   ├── home_assistant.py      # HA REST event bus and logbook egress
│   ├── ring.py                # Ring stream integration
│   └── web/                   # FastAPI ASGI server, routes, SSE hub, SPA
└── config/                    # Pydantic schema validation and YAML loader
    ├── schema.py              # LookoutConfig schema
    └── loader.py              # File loader with env var substitution
```

---

## 3. Port Protocols Specification

### Ingress Ports (`src/lookout/ports/ingress.py`)
- **`EventIngressPort`**:
  - `ingest_raw_event(camera_id: str, payload: bytes | dict[str, Any]) -> CameraEvent`
  - `process_event(event: CameraEvent) -> CameraEvent`

### Egress Ports (`src/lookout/ports/egress.py`)
- **`EventRepositoryPort`**: Persistence, querying, filtering, and deletion of camera events.
- **`VisionInferencePort`**: Scene analysis, bounding boxes, category classification, and confidence scoring.
- **`FaceRecognitionPort`**: Known face matching against enrolled resident gallery.
- **`AudioClassifierPort`**: YAMNet acoustic anomaly detection (glass break, scream, alarm).
- **`VideoRecorderPort`**: Snapshot capture and clip recording.
- **`MediaTranscoderPort`**: Video transcoding (e.g. RTSP to MP4 H.264/AAC).
- **`NotificationPort`**: Alert dispatch to external endpoints (ntfy, Home Assistant, Pushover).

---

## 4. Extending Lookout: Step-by-Step Tutorials

### How to Add a New Camera Adapter
1. **Define Adapter Class**:
   Create a new module in `src/lookout/adapters/my_camera.py`:
   ```python
   from lookout.ports.egress import VideoRecorderPort


   class MyCameraAdapter(VideoRecorderPort):
       def __init__(self, host: str, token: str) -> None:
           self.host = host
           self.token = token

       async def capture_snapshot(self, camera_id: str) -> str:
           # Capture snapshot image and return file path or URI
           return f"/data/snapshots/{camera_id}_snapshot.jpg"

       async def record_clip(self, camera_id: str, duration: float = 10.0) -> str:
           # Record short video clip and return file path or URI
           return f"/data/clips/{camera_id}_clip.mp4"
   ```

2. **Verify Protocol Conformance**:
   Add a test in `tests/test_ports_and_adapters.py`:
   ```python
   from lookout.ports.egress import VideoRecorderPort
   from lookout.adapters.my_camera import MyCameraAdapter


   def test_my_camera_implements_video_recorder_port():
       adapter = MyCameraAdapter(host="camera.local", token="secret")
       assert isinstance(adapter, VideoRecorderPort)
   ```

3. **Wire into Application**:
   Expose configuration in `lookout.config.schema` and inject into `EventProcessingService`.

---

### How to Add a New AI Inference Adapter
1. **Implement `VisionInferencePort`**:
   ```python
   from lookout.ports.egress import VisionInferencePort
   from lookout.domain.models import EventCategory, DetectionMetrics, InferenceProvenance


   class CoralEdgeTpuVisionAdapter(VisionInferencePort):
       async def analyze_scene(
           self, image_bytes: bytes
       ) -> tuple[EventCategory, DetectionMetrics, InferenceProvenance]:
           # Run hardware-accelerated model inference
           metrics = DetectionMetrics(confidence=0.95, bounding_boxes=[])
           provenance = InferenceProvenance(
               provider="coral_edge_tpu",
               model_name="mobilenet_ssd_v2",
               latency_ms=12.4,
               confidence=0.95,
           )
           return EventCategory.UNKNOWN_VISITOR, metrics, provenance
   ```

2. **Run Boundary Tests**:
   Run `uv run pytest tests/test_architecture.py` to ensure no illegal imports were introduced.

---

## 5. Architectural Fitness Tests

Architecture rules are automatically verified in CI using `tests/test_architecture.py`. These tests parse AST syntax trees to guarantee:
- `test_domain_has_no_adapter_or_external_imports`
- `test_ports_are_runtime_checkable`
- `test_ports_have_no_adapter_imports`
- `test_adapters_do_not_cross_import_siblings`
- `test_domain_has_no_web_framework_imports`
- `test_web_adapter_boundary_isolation`

For comprehensive testing documentation and details on the 3-tier Home Assistant testing architecture, see [docs/TESTING.md](TESTING.md).
