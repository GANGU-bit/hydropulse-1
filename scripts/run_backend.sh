#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root regardless of current working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/Scripts/activate
fi

ENV_MODE="${HYDROPULSE_ENV:-dev}"
HOST="${HYDROPULSE_HOST:-0.0.0.0}"
PORT="${HYDROPULSE_PORT:-8000}"
LOG_LEVEL="$(echo "${HYDROPULSE_LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"

# Validate configuration before starting Uvicorn
python -c "from backend.config import load_config; load_config()"

if [ "${ENV_MODE}" = "prod" ] || [ "${ENV_MODE}" = "production" ]; then
    # Single worker by default because active network revision & telemetry state live in-process
    exec uvicorn backend.main:app \
        --host "${HOST}" \
        --port "${PORT}" \
        --workers 1 \
        --log-level "${LOG_LEVEL}" \
        --no-access-log
else
    RELOAD_FLAG="--reload"
    if [ "${HYDROPULSE_RELOAD:-true}" = "false" ] || [ "${HYDROPULSE_RELOAD:-1}" = "0" ]; then
        RELOAD_FLAG=""
    fi
    # shellcheck disable=SC2086
    exec uvicorn backend.main:app \
        --host "${HOST}" \
        --port "${PORT}" \
        --log-level "${LOG_LEVEL}" \
        ${RELOAD_FLAG}
fi
