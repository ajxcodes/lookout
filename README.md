# Lookout

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Hexagonal](https://img.shields.io/badge/architecture-hexagonal-green.svg)](docs/ARCHITECTURE.md)

**Lookout** is a lightweight, multi-modal, privacy-first smart home camera intelligence platform designed for edge and self-hosted environments. It runs entirely on local infrastructure with zero cloud dependencies, orchestrating multi-tier AI inference cascades, real-time event streaming, and native Home Assistant integration.

---

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [5-Minute Quickstart](#5-minute-quickstart)
- [Container Images & Registries](#container-images--registries)
- [Docker Compose Deployments](#docker-compose-deployments)
  - [Standalone Deployment](#standalone-deployment)
  - [Sidecar Camera Ecosystem](#sidecar-camera-ecosystem)
- [Home Assistant Integration](#home-assistant-integration)
  - [Native Add-On & Ingress](#native-add-on--ingress)
  - [Lovelace Dashboard Cards](#lovelace-dashboard-cards)
  - [Sidebar Ingress Panel](#sidebar-ingress-panel)
  - [Camera Motion Automation Blueprint](#camera-motion-automation-blueprint)
- [Configuration Reference](#configuration-reference)
- [Troubleshooting & FAQs](#troubleshooting--faqs)
- [Contributing & Developer Guide](#contributing--developer-guide)

---

## Architecture Overview

Lookout is built on **Hexagonal Architecture** (Ports and Adapters), guaranteeing strict separation between core domain business rules and external I/O adapters:

```
               +-------------------------------------------+
               |              Lookout Domain               |
               |                                           |
               |   CameraEvent   ThreatLevel   Cascades    |
               +-------------------------------------------+
                        ^                         |
           (Ingress)    |                         | (Egress)
    +-------------------+----+               +----+-------------------+
    |     FastAPI / Web      |               |  SQLite / WAL Repo     |
    |  REST API & Ingress    |               |  FFmpeg Transcoder     |
    |  SSE Broadcast Hub     |               |  CascadeVisionAdapter  |
    |  Setup Wizard SPA      |               |  HomeAssistantEgress   |
    +------------------------+               +------------------------+
```

For in-depth architectural specifications and tutorials on adding custom adapters, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 5-Minute Quickstart

Get Lookout running locally in under 5 minutes:

### Prerequisites
- Python 3.12+ and [uv](https://github.com/astral-sh/uv) **OR** [Docker](https://docs.docker.com/engine/install/) & Docker Compose.
- FFmpeg installed locally (if running outside Docker).

### Option A: Running with Docker (Recommended)
```bash
# Clone the repository
git clone https://github.com/ajxcodes/lookout.git
cd lookout

# Launch Lookout standalone container
docker compose up -d

# Open the Web Viewer SPA or Setup Wizard
open http://localhost:8000
```

### Option B: Running with Python & UV
```bash
git clone https://github.com/ajxcodes/lookout.git
cd lookout

# Sync dependencies into virtual environment
uv sync --extra dev

# Launch Lookout ASGI server
uv run python -m lookout
```

If no configuration file exists at startup, Lookout automatically redirects you to the interactive Setup Wizard at `http://localhost:8000/setup` to configure your cameras, AI models, and storage directories.

---

## Container Images & Registries

Lookout publishes official multi-architecture (`linux/amd64`, `linux/arm64`) OCI container images to both **GitHub Container Registry (GHCR)** and **Docker Hub**:

| Distribution | GHCR Image | Docker Hub Image | Architectures |
|---|---|---|---|
| **Standalone Lookout** | `ghcr.io/ajxcodes/lookout` | `ajxcodes/lookout` | `linux/amd64`, `linux/arm64` |
| **Home Assistant Add-On** | `ghcr.io/ajxcodes/lookout-ha-addon` | `ajxcodes/lookout-ha-addon` | `linux/amd64`, `linux/arm64` |

### Supported Version Tags
- `:latest` — Latest stable release from `main`
- `:vX.Y.Z` — Exact semantic release tag (e.g., `:v0.1.0`)
- `:X.Y.Z` — Bare SemVer release tag matching Home Assistant Supervisor lookup (e.g., `:0.1.0`)
- `:X.Y` — Floating minor version tag (e.g., `:0.1`)
- `:sha-<short>` — Immutable commit digest from `main`

### Quick Pull & Run
```bash
# Pull from GitHub Container Registry (GHCR)
docker pull ghcr.io/ajxcodes/lookout:latest

# Or pull from Docker Hub
docker pull ajxcodes/lookout:latest

# Run Standalone Lookout container
docker run -d --name lookout -p 8000:8000 -v $(pwd)/data:/data ghcr.io/ajxcodes/lookout:latest
```

---

## Docker Compose Deployments

### Standalone Deployment
Use `docker-compose.yml` for a zero-configuration single-container deployment:

```yaml
services:
  lookout:
    image: lookout:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lookout
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - LOOKOUT_CONFIG=/data/config.yaml
      - PYTHONUNBUFFERED=1
    volumes:
      - ./data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

Run with:
```bash
docker compose up -d
```

### Sidecar Camera Ecosystem
Native Wyze and Ring ingestion is the default: Lookout talks directly to the vendor cloud APIs
and streams media through an embedded go2rtc process that ships with the container. No sidecar
is required, and `go2rtc.binary_path` / `LOOKOUT_GO2RTC_BINARY` can point at another binary.

Sidecars remain supported as an optional fallback for deployments that already run them. Use
`docker-compose.sidecar.yml` to link Lookout with external stream ingestors:
- **alexxit/go2rtc**: WebRTC gateway and low-latency media hub.
- **idisposable/docker-wyze-bridge**: Wyze local RTSP bridge (using the `idisposable` fork with host networking).
- **tsightler/ring-mqtt**: Ring RTSP and MQTT bridge.

```bash
docker compose -f docker-compose.sidecar.yml up -d
```

---

## Home Assistant Integration

### Native Add-On & Ingress
Lookout can be installed as a native Home Assistant Add-on:
1. In Home Assistant, open **Settings** -> **Add-ons** -> **Add-on Store**.
2. Add repository: `https://github.com/ajxcodes/lookout`.
3. Select **Lookout**, click **Install**, and check **Show in sidebar**.
4. Access the web interface directly via Ingress without separate authentication.

### Lovelace Dashboard Cards
To embed live event viewer cards in your Lovelace dashboard, import the template from `templates/home_assistant_lovelace.yaml`:

```yaml
type: iframe
url: "http://lookout.local:8000"
title: "Lookout Camera Viewer"
aspect_ratio: "16:9"
```

### Sidebar Ingress Panel
If running Lookout standalone on your network, add this snippet to your `configuration.yaml` in Home Assistant:

```yaml
panel_iframe:
  lookout:
    title: "Lookout"
    icon: "mdi:cctv"
    url: "http://lookout.local:8000"
    require_admin: false
```

### Camera Motion Automation Blueprint
Import `templates/home_assistant_motion_blueprint.yaml` into Home Assistant to automatically trigger Lookout scene analysis when PIR or camera motion is detected.

The automation executes a REST command to Lookout:
```yaml
rest_command:
  lookout_trigger_event:
    url: "http://lookout.local:8000/api/v1/events/trigger"
    method: POST
    headers:
      Content-Type: "application/json"
    payload: '{"camera_id": "{{ camera_id }}", "source": "home_assistant_automation"}'
```

---

## Configuration Reference

Lookout is configured via a YAML file (default `config.yaml` or `/data/config.yaml`):

```yaml
system_name: "Lookout Smart Home"

storage:
  base_path: "./data"
  database_path: "./data/events.db"
  retention_days: 30

cascade:
  local_edge_model: "local-edge-vision-cpu"
  ollama_model: "moondream"
  confidence_threshold: 0.85

cameras:
  - id: "front-porch"
    name: "Front Porch"
    stream_url: "rtsp://localhost:8554/front_porch"
    enabled: true
    tier: "critical"

  - id: "backyard"
    name: "Backyard"
    stream_url: "rtsp://localhost:8554/backyard"
    enabled: true
    tier: "standard"
```

---

## Troubleshooting & FAQs

### Container fails health check (`/health` returns error)
- Ensure port 8000 is not blocked by another service.
- Verify container logs with `docker logs lookout`.
- Run curl manually inside container: `docker exec -it lookout curl -v http://localhost:8000/health`.

### Setup wizard keeps appearing
- Lookout enters setup wizard mode when `LOOKOUT_CONFIG` points to a non-existent file or when configuration fails to parse.
- Complete the onboarding wizard at `/setup` or ensure valid `config.yaml` is mounted at `/data/config.yaml`.

### Home Assistant Ingress shows blank page
- Verify that `ingress: true` and `ingress_port: 8000` are configured in `ha-addon/config.yaml`.
- Ensure reverse proxy headers (`X-Ingress-Path`) are forwarded.

---

## Testing & Local Smoke Tests

Lookout enforces a 3-tier Home Assistant testing architecture (Add-on linting, Core schema validation, and runtime network ingress verification).

You can run automated local smoke tests using Docker or Podman:
```bash
# Run local container and Home Assistant schema smoke tests
./scripts/smoke_ci.sh

# Validate local multi-architecture (linux/amd64, linux/arm64) Buildx / Podman manifest builds
./scripts/smoke_ci.sh --multiarch

# Run quality gates (Ruff, Mypy) and Playwright E2E browser suite
./scripts/smoke_ci.sh --e2e

# Simulate GitHub Actions workflow locally with nektos/act
./scripts/smoke_ci.sh --act
```

For complete documentation on quality gates and Home Assistant test fixtures, see [docs/TESTING.md](docs/TESTING.md).

---

## Contributing & Developer Guide
Contributions are welcome! Please review [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/TESTING.md](docs/TESTING.md) to understand port-and-adapter boundaries and testing protocols before submitting a pull request.
