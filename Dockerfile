# =============================================================================
# Resonance Music Server — Multi-Stage Dockerfile
# =============================================================================
# Stage 1: Build the Svelte Web-UI (Node.js)
# Stage 2: Python runtime with audio transcoding tools
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build Web-UI
# ---------------------------------------------------------------------------
FROM node:22-slim AS web-ui-builder

WORKDIR /build

# Install dependencies first (layer caching)
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci

# Copy source and build
COPY web-ui/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: Python Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

# Labels
LABEL maintainer="Resonance Contributors"
LABEL description="Resonance Music Server — a modern Squeezebox-compatible music server"
LABEL org.opencontainers.image.source="https://github.com/endegelaende/resonance-server"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime dependencies:
#   - Audio transcoding: faad, flac, lame, sox (with format libs), ffmpeg
#   - Pillow runtime dependencies (for ICO→PNG imageproxy conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
        faad \
        flac \
        lame \
        sox \
        libsox-fmt-all \
        ffmpeg \
        libjpeg62-turbo \
        libpng16-16 \
        libwebp7 \
        libtiff6 \
        libfreetype6 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN groupadd --gid 1000 resonance \
    && useradd --uid 1000 --gid resonance --shell /bin/bash --create-home resonance

# Create app directory
WORKDIR /app

# Install Python dependencies (layer caching: copy only pyproject.toml + README first)
# Create stub dirs so hatch force-include paths resolve during wheel build.
# The real content is COPY'd below and overlays /app at runtime.
COPY pyproject.toml README.md ./
RUN mkdir -p plugins static web-ui/build \
    && pip install --no-cache-dir ".[blurhash]"

# Copy application code
# Note: resonance/ includes config/legacy.conf and config/devices.toml
COPY resonance/ ./resonance/
COPY plugins/ ./plugins/
COPY static/ ./static/
COPY assets/ ./assets/

# Copy built Web-UI from Stage 1
COPY --from=web-ui-builder /build/build/ ./web-ui/build/

# Create directories for persistent data and cache, owned by non-root user
RUN mkdir -p /app/data /app/cache /music \
    && chown -R resonance:resonance /app /music

# Switch to non-root user
USER resonance

# --- Ports ---
# 3483/tcp  — Slimproto (player control)
# 3483/udp  — Slimproto discovery
# 9000/tcp  — Web UI / HTTP streaming / JSON-RPC
# 9090/tcp  — CLI (Telnet)
EXPOSE 3483/tcp 3483/udp 9000 9090

# --- Volumes ---
# /music      — Music library (mount read-only from host)
# /app/data   — Persistent data (playlists, alarms, player prefs, plugin data)
# /app/cache  — Cache (database, artwork cache, server UUID)
VOLUME ["/music", "/app/data", "/app/cache"]

# --- Health Check ---
# Probe the web server; adjust timeout for slow starts on first scan
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/api/status')" || exit 1

# --- Entrypoint ---
# Run as the resonance module; all CLI flags are passable via CMD / docker-compose
ENTRYPOINT ["python", "-m", "resonance"]

# Default arguments (can be overridden in docker-compose or docker run)
CMD ["--host", "0.0.0.0", "--web-port", "9000", "--cli-port", "9090"]
