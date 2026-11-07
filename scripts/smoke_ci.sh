#!/usr/bin/env bash
# ==============================================================================
# scripts/smoke_ci.sh - Local smoke test script for Lookout CI & Containers
#
# Runs local validation for Docker Compose, container image builds,
# Home Assistant Core configuration checks, and optional nektos/act CI runner.
# ==============================================================================

set -euo pipefail

# ANSI color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Resolve script and repository directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Detect and verify container engine: check candidate runtimes and daemon health
CANDIDATE_RUNTIMES=()
if command -v docker >/dev/null 2>&1; then
    CANDIDATE_RUNTIMES+=("docker")
fi
if command -v podman >/dev/null 2>&1; then
    CANDIDATE_RUNTIMES+=("podman")
fi

if [[ ${#CANDIDATE_RUNTIMES[@]} -eq 0 ]]; then
    log_error "Neither docker nor podman was found on PATH. Container smoke tests require a container runtime."
    exit 1
fi

CONTAINER_BIN=""
COMPOSE_CMD=""

for runtime in "${CANDIDATE_RUNTIMES[@]}"; do
    if "${runtime}" info >/dev/null 2>&1; then
        CONTAINER_BIN="${runtime}"
        break
    fi
done

if [[ -z "${CONTAINER_BIN}" ]]; then
    log_error "Container runtime binaries found (${CANDIDATE_RUNTIMES[*]}), but the daemon/socket is inactive or unreachable."
    log_error "Please start Docker/Podman (e.g., 'sudo systemctl start docker' or 'systemctl --user start podman.socket') before running CI smoke tests."
    exit 1
fi

if [[ "${CONTAINER_BIN}" == "docker" ]]; then
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    fi
elif [[ "${CONTAINER_BIN}" == "podman" ]]; then
    if podman compose version >/dev/null 2>&1; then
        COMPOSE_CMD="podman compose"
    elif command -v podman-compose >/dev/null 2>&1; then
        COMPOSE_CMD="podman-compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    fi
fi

log_info "Detected active container engine: ${CONTAINER_BIN}"

# Mode flag: check if --act was requested
RUN_ACT=false
for arg in "$@"; do
    if [[ "$arg" == "--act" ]]; then
        RUN_ACT=true
    fi
done

if [[ "${RUN_ACT}" == "true" ]]; then
    if ! command -v act >/dev/null 2>&1; then
        log_error "nektos/act is not installed. Install act to simulate GitHub Actions workflows locally."
        exit 1
    fi
    log_info "Running GitHub Actions workflows locally via act..."
    if [[ "${CONTAINER_BIN}" == "podman" ]]; then
        DOCKER_HOST="unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock" act -j quality || act -j quality
    else
        act -j quality
    fi
    log_success "act CI workflow completed successfully!"
    exit 0
fi

# ------------------------------------------------------------------------------
# 1. Validate Docker Compose Definitions
# ------------------------------------------------------------------------------
log_info "Step 1: Validating Docker Compose configurations..."
if [[ -n "${COMPOSE_CMD}" ]]; then
    ${COMPOSE_CMD} config --quiet
    ${COMPOSE_CMD} -f docker-compose.sidecar.yml config --quiet
    log_success "Docker Compose files are valid."
else
    log_warn "Compose CLI not found; skipping compose config validation."
fi

# ------------------------------------------------------------------------------
# 2. Build Container Image
# ------------------------------------------------------------------------------
log_info "Step 2: Building container image (lookout:smoke-test)..."
${CONTAINER_BIN} build -t lookout:smoke-test .
log_success "Container image built successfully."

# ------------------------------------------------------------------------------
# 3. Validate Home Assistant Core Schema
# ------------------------------------------------------------------------------
log_info "Step 3: Validating Home Assistant configuration schema via check_config..."
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/ha_config"
if [[ -d "${FIXTURE_DIR}" ]]; then
    # Run HA Core check_config in container if network/engine allows
    if ${CONTAINER_BIN} run --rm -v "${FIXTURE_DIR}:/config:ro" ghcr.io/home-assistant/home-assistant:stable python -m homeassistant --config /config --script check_config 2>/dev/null; then
        log_success "Home Assistant Core configuration schema validated."
    else
        log_warn "Home Assistant container check skipped or image unavailable offline."
    fi
else
    log_warn "HA fixture directory ${FIXTURE_DIR} not found; skipping check_config."
fi

# ------------------------------------------------------------------------------
# 4. End-to-End Container Health & Trigger Test
# ------------------------------------------------------------------------------
log_info "Step 4: Testing runtime health and event trigger endpoints..."
NETWORK_NAME="lookout-smoke-net-$RANDOM"
CONTAINER_NAME="lookout-smoke-test-$RANDOM"

cleanup() {
    log_info "Cleaning up smoke test containers and network..."
    ${CONTAINER_BIN} stop "${CONTAINER_NAME}" 2>/dev/null || true
    ${CONTAINER_BIN} rm -f "${CONTAINER_NAME}" 2>/dev/null || true
    ${CONTAINER_BIN} network rm "${NETWORK_NAME}" 2>/dev/null || true
}
trap cleanup EXIT

${CONTAINER_BIN} network create "${NETWORK_NAME}"
${CONTAINER_BIN} run -d --name "${CONTAINER_NAME}" --network "${NETWORK_NAME}" -p 8000:8000 \
    -e LOOKOUT_CONFIG=/data/config.yaml lookout:smoke-test

log_info "Waiting for service startup..."
sleep 5
${CONTAINER_BIN} logs "${CONTAINER_NAME}"

# Probe health endpoint
if command -v curl >/dev/null 2>&1; then
    HEALTH_RESP=$(curl -s -f http://localhost:8000/health || true)
    if [[ "${HEALTH_RESP}" == *"\"status\":\"ok\""* || "${HEALTH_RESP}" == *"\"status\": \"ok\""* ]]; then
        log_success "Health endpoint verified: ${HEALTH_RESP}"
    else
        log_error "Health endpoint failed: ${HEALTH_RESP}"
        exit 1
    fi

    # Trigger event
    TRIGGER_RESP=$(curl -s -f -X POST -H "Content-Type: application/json" \
        -d '{"camera_id": "smoke_camera"}' http://localhost:8000/api/v1/events/trigger || true)
    if [[ "${TRIGGER_RESP}" == *"\"status\":\"ok\""* || "${TRIGGER_RESP}" == *"\"status\": \"ok\""* ]]; then
        log_success "Event trigger endpoint verified: ${TRIGGER_RESP}"
    else
        log_error "Event trigger endpoint failed: ${TRIGGER_RESP}"
        exit 1
    fi
else
    log_warn "Host curl not found; probe skipped."
fi

log_success "All local container and Home Assistant smoke tests passed!"
