# Lookout Testing & CI Guide

This document describes Lookout's automated quality pipeline, multi-tier Home Assistant testing architecture, and local smoke testing tools.

---

## 1. Overview of Quality Gates

Lookout enforces a strict quality gate across all pull requests and releases:
1. **Code Formatting**: Ruff (`uv run ruff format --check .`)
2. **Linting**: Ruff (`uv run ruff check .`)
3. **Type Safety**: Mypy strict mode (`uv run mypy src`)
4. **Unit & Integration Tests**: Pytest with coverage (`uv run pytest --cov=lookout`)
5. **Architectural Fitness**: AST boundary isolation tests (`tests/test_architecture.py`)
6. **Container & Home Assistant Integration**: Automated container packaging and HA linter (`.github/workflows/ci.yml`)

---

## 2. Three-Tier Home Assistant Testing Architecture

To ensure flawless interoperability with Home Assistant OS, Supervised, and Container deployments without risking runtime regressions, Lookout employs a **3-tier testing strategy**:

```
+-------------------------------------------------------------------------+
| Tier 1: Add-on Metadata & Manifest Linting                              |
| frenck/action-addon-linter validates ha-addon/config.yaml & DOCS.md     |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Tier 2: Home Assistant Core Schema Validation                           |
| check_config validates configuration.yaml, panel_iframe & blueprints    |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Tier 3: Runtime Network Ingress & Ingestion Verification                |
| Live container healthcheck & REST command trigger verification          |
+-------------------------------------------------------------------------+
```

### Tier 1: Add-on Metadata & Manifest Linting
- **Tool**: `frenck/action-addon-linter@v2`
- **Scope**: Inspects `ha-addon/config.yaml`, `ha-addon/build.yaml`, and `ha-addon/DOCS.md`.
- **Guarantees**: Validates schema compliance, ingress port mapping (`ingress: true`, `ingress_port: 8000`), supported architectures (`amd64`, `aarch64`), and directory access privileges (`config:rw`, `share:rw`, `media:rw`).

### Tier 2: Home Assistant Core Schema Validation
- **Tool**: Official container `ghcr.io/home-assistant/home-assistant:stable`
- **Command**: `python -m homeassistant --config tests/fixtures/ha_config --script check_config`
- **Guarantees**: Validates that all Home Assistant integration templates—including `panel_iframe`, `rest_command.lookout_trigger_event`, and camera motion blueprints—conform to live Home Assistant Core component schemas.

### Tier 3: Runtime Network Ingress & Ingestion Verification
- **Tool**: Container bridge network with `curlimages/curl` or host test client.
- **Endpoints**:
  - `GET /health`: Asserts HTTP 200 and `{"status": "ok", "configured": ..., "version": "0.1.0"}`.
  - `POST /api/v1/events/trigger`: Simulates incoming Home Assistant camera trigger webhook, confirming event ingestion into `EventProcessingService` and real-time SSE broadcast.

---

## 3. Running Local Smoke Tests

You can run the entire smoke test suite on your local machine using the provided script [`scripts/smoke_ci.sh`](../scripts/smoke_ci.sh).

### Prerequisites
- Docker **OR** Podman.
- (Optional) [nektos/act](https://github.com/nektos/act) for running GitHub Actions workflows locally.

### Standard Container Smoke Test
Run:
```bash
./scripts/smoke_ci.sh
```
This script automatically:
1. Detects `docker` or `podman` runtime.
2. Validates standalone and sidecar `docker-compose` files.
3. Builds the local container image (`lookout:smoke-test`).
4. Runs Home Assistant Core `check_config` against test fixtures.
5. Launches a temporary sandboxed container, probes `GET /health`, fires `POST /api/v1/events/trigger`, and cleans up all resources.

### Simulating GitHub Actions via `act`
To run the GitHub Actions workflow locally using `nektos/act`:
```bash
./scripts/smoke_ci.sh --act
```
Or directly:
```bash
act -j quality
act -j container-and-ha
```
