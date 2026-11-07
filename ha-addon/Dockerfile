ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ARG RELEASE_VERSION=""
LABEL org.opencontainers.image.source="https://github.com/ajxcodes/lookout"
LABEL org.opencontainers.image.version="${RELEASE_VERSION}"

# Install Astral uv and system dependencies
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application files and install
COPY . /app/
RUN uv pip install --system --no-cache /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "lookout.adapters.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
