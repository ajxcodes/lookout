# syntax=docker/dockerfile:1

# ------------------------------------------------------------------------------
# Stage 1: Builder
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Copy Astral uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install project dependencies with layer caching
COPY pyproject.toml uv.lock package.json ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source and build/install project wheel
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# ------------------------------------------------------------------------------
# Stage 2: Ring CLI (pinned Node 20 + ring-client-api@14.3.0)
# ------------------------------------------------------------------------------
# ring-client-api@14.3.0 requires Node ^20 || ^22 || ^24. Debian bookworm's apt
# `nodejs` is v18, which is too old, so use the pinned upstream Node 20 image
# (bookworm-slim, matching the runtime base) and install the package there.
FROM node:20-bookworm-slim AS ring-cli

WORKDIR /opt/ring-cli
COPY package.json ./
RUN npm install --omit=dev --save-exact ring-client-api@14.3.0 && \
    node --version && \
    test -x /opt/ring-cli/node_modules/.bin/ring-auth-cli && \
    node -e "require('ring-client-api')"

# ------------------------------------------------------------------------------
# Stage 3: Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ARG RELEASE_VERSION=""
# BEGIN LOOKOUT VERSION FALLBACK (managed by scripts/sync_version.py)
ENV RELEASE_VERSION="${RELEASE_VERSION:-0.7.0}"
# END LOOKOUT VERSION FALLBACK
ARG GO2RTC_VERSION=1.9.14
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/ajxcodes/lookout"
LABEL org.opencontainers.image.version="${RELEASE_VERSION}"

# Install system dependencies (FFmpeg, Node.js 20 LTS, OpenCV/graphics runtime, curl, ca-certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    libstdc++6 \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install the embedded go2rtc binary used for native Wyze/Ring streaming.
RUN if [ -z "${TARGETARCH}" ]; then TARGETARCH="$(dpkg --print-architecture)"; fi && \
    case "${TARGETARCH}" in \
        amd64) GO2RTC_ARCH="amd64" ;; \
        arm64) GO2RTC_ARCH="arm64" ;; \
        arm|armv7) GO2RTC_ARCH="arm" ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2 && exit 1 ;; \
    esac && \
    curl -fsSL -o /usr/local/bin/go2rtc \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${GO2RTC_ARCH}" && \
    chmod +x /usr/local/bin/go2rtc && \
    go2rtc --version

# Create non-root lookout user and group (UID 1000, GID 1000)
RUN groupadd -g 1000 lookout && \
    useradd -u 1000 -g lookout -m -s /bin/bash lookout

# Create persistent storage directories and application root with proper ownership
RUN mkdir -p /data/faces /data/media && chown -R lookout:lookout /data && mkdir -p /app && chown -R lookout:lookout /app

# Provision the real Ring CLI: copy the pinned Node 20 runtime binary and the
# installed ring-client-api dependency tree (including its .bin symlinks) into
# /app, where the default RingAuthCliIssuer expects
# /app/node_modules/.bin/ring-auth-cli. The tree is owned by lookout so the
# non-root process can execute the CLI.
COPY --from=ring-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=ring-cli --chown=lookout:lookout /opt/ring-cli/node_modules /app/node_modules

# Build-time assertion: Node 20, the CLI executable, and the module all resolve.
# Use an absolute require path because WORKDIR /app is declared further below.
RUN node --version && \
    test -x /app/node_modules/.bin/ring-auth-cli && \
    node -e "require('/app/node_modules/ring-client-api')"

# ring-client-api 14.3.0's exports map only exposes "./rest-client", but the
# bundled auth helper imports the concrete "lib/rest-client.js" subpath. Mirror
# that subpath in the map so both ESM specifiers resolve to the same module.
RUN node -e "const fs = require('fs'); const p = '/app/node_modules/ring-client-api/package.json'; const manifest = JSON.parse(fs.readFileSync(p, 'utf8')); manifest.exports['./lib/rest-client.js'] = './lib/rest-client.js'; fs.writeFileSync(p, JSON.stringify(manifest, null, 2) + '\n')"

WORKDIR /app

# Install ring-client-api for the H.264 live stream bridge
RUN npm install --no-save --omit=dev ring-client-api && chown -R lookout:lookout /app/node_modules

# Copy virtual environment and application from builder
COPY --from=builder --chown=lookout:lookout /app/.venv /app/.venv
COPY --from=builder --chown=lookout:lookout /app/src /app/src
COPY --from=builder --chown=lookout:lookout /app/package.json /app/package.json

# Set environment paths and configuration
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOOKOUT_CONFIG=/data/config.yaml

# Run as non-root user
USER lookout

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "lookout.adapters.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
