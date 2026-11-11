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
# Stage 2: Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ARG RELEASE_VERSION=""
ARG GO2RTC_VERSION=1.9.14
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/ajxcodes/lookout"
LABEL org.opencontainers.image.version="${RELEASE_VERSION}"

# Install system dependencies (FFmpeg, OpenCV/graphics runtime, curl, ca-certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates \
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

WORKDIR /app

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
