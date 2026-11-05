# =============================================================================
# HydroPulse Backend — Production Linux Container Image
# Target Runtime: 64-bit CPython 3.11 + WNTR 1.5.0 + FastAPI / Uvicorn
# =============================================================================
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System build dependencies for scientific Python wheels if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /build/requirements.txt
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip wheel && \
    /opt/venv/bin/pip install -r /build/requirements.txt


# -----------------------------------------------------------------------------
# Production Runtime Stage
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HYDROPULSE_ENV="prod" \
    HYDROPULSE_HOST="0.0.0.0" \
    HYDROPULSE_PORT="8000" \
    HYDROPULSE_LOG_LEVEL="INFO" \
    HYDROPULSE_RELOAD="false" \
    HYDROPULSE_STATE_DIR="/app/backend/.state"

# Runtime shared libraries required by WNTR / SciPy / pyproj / OSMnx on Debian Bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libexpat1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user and group
RUN groupadd --gid 10001 hydropulse && \
    useradd --uid 10001 --gid hydropulse --create-home --shell /usr/sbin/nologin hydropulse

WORKDIR /app

# Copy pre-built virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy only runtime backend and data_utils files (see .dockerignore)
COPY --chown=hydropulse:hydropulse backend/ /app/backend/
COPY --chown=hydropulse:hydropulse data_utils/ /app/data_utils/
COPY --chown=hydropulse:hydropulse scripts/run_backend.sh /app/scripts/run_backend.sh
COPY --chown=hydropulse:hydropulse SOURCE_OF_TRUTH.md /app/SOURCE_OF_TRUTH.md

# Ensure writable state/cache directory for non-root user and pre-warm sensitivity matrix cache
RUN mkdir -p /app/backend/.state && \
    chown -R hydropulse:hydropulse /app/backend && \
    chmod +x /app/scripts/run_backend.sh

USER hydropulse:hydropulse

# Verify WNTR/EPANET C-extension and pre-build the sensitivity matrix cache during image build
RUN python -c "from backend.hydraulic_engine import HydraulicEngine; eng = HydraulicEngine().boot(); assert eng.health()['matrix_ready'] is True"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, json, sys; r = json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).read()); sys.exit(0 if r.get('status') == 'ok' and r.get('matrix_ready') else 1)"

# Single worker by default: active network revision and telemetry ring buffer are in-process
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
