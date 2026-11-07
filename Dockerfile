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
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source and build/install project wheel
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# ------------------------------------------------------------------------------
# Stage 2: Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

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

# Create non-root lookout user and group (UID 1000, GID 1000)
RUN groupadd -g 1000 lookout && \
    useradd -u 1000 -g lookout -m -s /bin/bash lookout

# Create persistent storage directories with proper ownership
RUN mkdir -p /data/faces && chown -R lookout:lookout /data

WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder --chown=lookout:lookout /app/.venv /app/.venv
COPY --from=builder --chown=lookout:lookout /app/src /app/src

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
