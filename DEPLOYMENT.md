# HydroPulse Backend — Deployment & Operations Guide (v1.1)

This document covers the production deployment architecture, Linux environment setup, runtime configuration, security hardening, OpenRouter AI gateway integration, container packaging, and operational verification for the HydroPulse v1.1 backend.

---

## 1. Supported Runtime & Architecture

- **Language & Runtime**: 64-bit CPython **3.11** (tested on CPython 3.11.16 on Arch Linux `x86_64` and `python:3.11-slim` Debian Bookworm containers).
- **Web Framework**: FastAPI + Uvicorn ASGI (`backend.main:app`).
- **Hydraulic Engine**: US EPA WNTR `1.5.0` (steady-state Pressure-Driven Demand + Torricelli orifice leak physics).
- **Concurrency & Worker Model**:
  - **Single-worker process (`--workers 1`) is required** unless external shared storage is introduced.
  - Why: `HydraulicEngine` (`backend/hydraulic_engine.py`), `NetworkBuilderManager` (`backend/network_builder.py`), and `IoTTelemetryManager` (`backend/iot_manager.py`) hold in-memory active network revisions, preview tokens, and telemetry ring buffers protected by in-process `threading.RLock()`.
  - FastAPI executes synchronous hydraulic endpoints in a threadpool; every WNTR simulation acquires the engine's re-entrant lock or runs on an isolated `WaterNetworkModel` clone (`clone_for_scenario`), guaranteeing thread safety across concurrent HTTP requests within the single worker.

---

## 2. Arch Linux & Local Development Setup

```bash
# 1. Ensure Python 3.11 venv is active and dependencies are installed
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 2. Verify startup configuration
python -c "from backend.config import load_config; print(load_config().safe_summary())"

# 3. Start in development mode (with auto-reload)
./scripts/run_backend.sh
```

---

## 3. Environment Variables Reference

Copy `.env.example` to `.env` for local overrides or inject environment variables directly via Docker / systemd. **Never commit `.env` or real secrets to Git.**

| Variable | Default (`dev`) | Default (`prod`) | Description |
| :--- | :--- | :--- | :--- |
| `HYDROPULSE_ENV` | `dev` | `prod` | Runtime environment (`dev` or `prod`). |
| `HYDROPULSE_HOST` | `0.0.0.0` | `0.0.0.0` | Bind address for Uvicorn. |
| `HYDROPULSE_PORT` | `8000` | `8000` | Bind TCP port (`1..65535`). |
| `HYDROPULSE_LOG_LEVEL` | `INFO` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `HYDROPULSE_RELOAD` | `false` (`true` in `run_backend.sh` dev) | `false` (**forbidden** in `prod`) | Auto-reload on code changes. Startup fails if `true` when `HYDROPULSE_ENV=prod`. |
| `HYDROPULSE_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173` | **Required explicit origin(s)** | Comma-separated allowed CORS origins. Wildcard `*` is rejected in `prod`. |
| `HYDROPULSE_INP` | `backend/bangalore_kengeri.inp` | `backend/bangalore_kengeri.inp` | Path to baseline EPANET `.inp` file (resolved relative to repo root). |
| `HYDROPULSE_META` | `backend/network_meta.json` | `backend/network_meta.json` | Path to baseline network metadata JSON. |
| `HYDROPULSE_PRESETS` | `data_utils/presets.json` | `data_utils/presets.json` | Path to demo leak presets JSON. |
| `HYDROPULSE_CACHE_DIR` | `backend` | `backend` | Directory for `.npy` sensitivity matrix caches. |
| `HYDROPULSE_STATE_DIR` | `backend/.state` | `backend/.state` | Directory for persisted active network state (`active_network.inp`, `active_state.json`). |
| `HYDROPULSE_PERSIST_ACTIVE_STATE` | `false` | `true` | Persist committed network imports/builds/suggestions across process restarts. |
| `HYDROPULSE_REQUIRE_ADMIN_AUTH` | `false` | `true` (if `HYDROPULSE_ADMIN_TOKEN` set) | Require admin token on mutating endpoints (`/commit`, `/reset`, `/apply-suggestion`, `/auto-valve`, `/ingest`). |
| `HYDROPULSE_ADMIN_TOKEN` | *(unset)* | *(secret, >= 8 chars)* | Shared bearer/header secret checked via `X-Admin-Token: <token>` or `Authorization: Bearer <token>`. |
| `HYDROPULSE_MAX_REQUEST_BYTES` | `2621440` (2.5 MiB) | `2621440` (2.5 MiB) | Maximum HTTP request payload size (`65536..20971520`). |
| `HYDROPULSE_DOCS_ENABLED` | `true` | `false` | Expose `/docs`, `/redoc`, and `/openapi.json`. |
| `HYDROPULSE_OPENROUTER_ENABLED` / `OPENROUTER_ENABLED` | `true` | `true` | Enable optional server-side OpenRouter narrative enrichment when `OPENROUTER_API_KEY` is present. |
| `OPENROUTER_API_KEY` | *(unset)* | *(optional secret)* | Server-side OpenRouter API key. Never exposed to the browser or logs. |
| `HYDROPULSE_OPENROUTER_MODEL` / `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct:free` | `meta-llama/llama-3.3-70b-instruct:free` | Primary OpenRouter model ID (defaults to `:free` tier). |
| `HYDROPULSE_OPENROUTER_FALLBACK_MODELS` / `OPENROUTER_FALLBACK_MODELS` | `qwen/qwen-2.5-72b-instruct:free,mistralai/mistral-small-3.1-24b-instruct:free` | Same | Comma-separated `:free` fallback models on 429/5xx. |
| `HYDROPULSE_OPENROUTER_BASE_URL` / `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | `https://openrouter.ai/api/v1` | OpenRouter API gateway URL. Direct Gemini/Groq/OpenAI/Anthropic URLs are rejected at startup. |
| `HYDROPULSE_OPENROUTER_TIMEOUT_S` / `OPENROUTER_TIMEOUT_S` | `4.0` | `4.0` | Read timeout in seconds (`0.2..30.0`). |
| `HYDROPULSE_OPENROUTER_CONNECT_TIMEOUT_S` / `OPENROUTER_CONNECT_TIMEOUT_S` | `2.0` | `2.0` | Connect timeout in seconds (`0.1..15.0`). |
| `HYDROPULSE_OPENROUTER_MAX_RETRIES` / `OPENROUTER_MAX_RETRIES` | `1` | `1` | Bounded retry count per model (`0..3`). |

---

## 4. Production Startup Procedure (Non-Container)

```bash
export HYDROPULSE_ENV=prod
export HYDROPULSE_HOST=0.0.0.0
export HYDROPULSE_PORT=8000
export HYDROPULSE_RELOAD=false
export HYDROPULSE_CORS_ORIGINS="https://hydropulse.example.com"
export HYDROPULSE_REQUIRE_ADMIN_AUTH=true
export HYDROPULSE_ADMIN_TOKEN="replace-with-strong-random-token-at-least-16-chars"
export HYDROPULSE_PERSIST_ACTIVE_STATE=true

# Optional: enable OpenRouter narrative enrichment on /api/ai/suggestions
# export OPENROUTER_API_KEY="sk-or-v1-..."

./scripts/run_backend.sh
```

In `prod` mode, `scripts/run_backend.sh` validates `load_config()` before launching Uvicorn **without** `--reload`.

---

## 5. Container Build & Run (Docker / Podman)

A production `Dockerfile` and `.dockerignore` are provided at the repository root. Note: the build workstation (`linux 7.2.14-arch1-1`) does not have the `docker` or `podman` daemon installed locally, so container builds are verified in GitHub Actions CI (`.github/workflows/backend-ci.yml`).

```bash
# Build production image
docker build -t hydropulse-backend:1.1.0 .

# Run production container with persistent volume for .state/ and sensitivity caches
docker run -d \
  --name hydropulse-backend \
  -p 8000:8000 \
  -e HYDROPULSE_ENV=prod \
  -e HYDROPULSE_CORS_ORIGINS="http://localhost:5173" \
  -e HYDROPULSE_PERSIST_ACTIVE_STATE=true \
  -v hydropulse_state:/app/backend/.state \
  hydropulse-backend:1.1.0
```

Container security features:
- Runs as non-root Linux user `hydropulse` (UID `10001`).
- Excludes `.git`, `.venv`, `.env*`, frontend sources, and test artifacts via `.dockerignore`.
- Includes a built-in `HEALTHCHECK` probing `http://127.0.0.1:8000/api/health`.

---

## 6. Health & Readiness Checks

HydroPulse provides two operational probes:

1. **Liveness (`GET /api/health`)** — Contract v1.0 & v1.1 compatible:
   ```bash
   curl -sSf http://127.0.0.1:8000/api/health | python3 -m json.tool
   ```
   Returns `status: "ok"`, `network_name`, `num_junctions`, `num_pipes`, `sensor_ids`, `matrix_ready`, `network_revision`, `source`, and `can_rollback`.

2. **Readiness (`GET /api/ready`)** — Deployment readiness gate:
   ```bash
   curl -sSf http://127.0.0.1:8000/api/ready | python3 -m json.tool
   ```
   Returns HTTP `200` (`status: "ready"`) only when the WNTR hydraulic baseline and sensitivity matrix are fully computed/loaded and ready to triangulate bursts. Returns HTTP `503` if still initializing.

---

## 7. OpenRouter Architecture & Fallback Guarantees

- **Exclusive Gateway**: OpenRouter (`https://openrouter.ai/api/v1`) is the sole external AI integration (`backend/openrouter_client.py`). Direct Gemini, Groq, OpenAI, or Anthropic URLs are blocked by `BackendConfig.validate()`.
- **Deterministic Physics Core**: All hydraulic simulations, leak triangulation, What-If counterfactuals, pipe RUL estimates, and AI engineering recommendations (`loop_completion`, `pipe_upsizing`, `sensor_placement` in `backend/ai_advisor.py`) are 100% deterministic physics and graph algorithms.
- **Zero-Breakage Fallback**: When `OPENROUTER_API_KEY` is unset, disabled, rate-limited (`429`), or times out, `/api/ai/suggestions` immediately returns the deterministic engineering rationale without raising errors or slowing down the API beyond `HYDROPULSE_OPENROUTER_TIMEOUT_S`.

---

## 8. Security & Administrative Controls

### Endpoint Classification
- **Public Read-Only (`GET`)**: `/api/health`, `/api/ready`, `/api/network`, `/api/presets`, `/api/network/presets`, `/api/ai/suggestions`, `/api/iot/state`, `/api/pipes/condition`.
- **Public Stateless Simulation / Preview (`POST`)**: `/api/simulate-leak`, `/api/isolate`, `/api/what-if`, `/api/network/import/preview`, `/api/network/import`, `/api/network/build/preview`, `/api/network/intellisense`.
- **Administrative / State-Mutating (`POST`)**:
  - `/api/network/import/commit`
  - `/api/network/build/commit`
  - `/api/network/reset`
  - `/api/ai/apply-suggestion`
  - `/api/iot/ingest`
  - `/api/iot/auto-valve`

### Two-Layer Mutation Protection
1. **Explicit Confirmation Flag**: Mutating commit/reset/apply/auto-valve payloads require `confirm: true` in the JSON body (returns `400` or `409` on missing confirmation or stale `expected_network_revision`).
2. **Optional Server-Side Admin Token (`HYDROPULSE_REQUIRE_ADMIN_AUTH=true`)**: Requires `X-Admin-Token: <HYDROPULSE_ADMIN_TOKEN>` or `Authorization: Bearer <HYDROPULSE_ADMIN_TOKEN>` using constant-time `hmac.compare_digest`. Never place `HYDROPULSE_ADMIN_TOKEN` or `OPENROUTER_API_KEY` in frontend `VITE_*` variables.

### Input Hardening
- Request body size capped at `HYDROPULSE_MAX_REQUEST_BYTES` (default 2.5 MiB; returns HTTP `413`).
- `NaN`, `Infinity`, and `-Infinity` JSON values are rejected at the ASGI middleware boundary with HTTP `422`.
- Every response carries `X-Request-ID` and `X-Content-Type-Options: nosniff`. Unhandled exceptions in `prod` return a sanitized JSON error (`{"detail": "Internal server error (request_id=...)"}`) without leaking stack traces or file paths.

---

## 9. Persistence, Backup & Restart Behaviour

- **Sensitivity Matrix Cache**: Stored in `backend/sensitivity_matrix.npy` (and `.x.npy` sidecar) for the baseline network, and `backend/sensitivity_matrix_<sha256[:8]>.npy` for imported/mutated networks. Written atomically via temporary file + `os.replace` (`_atomic_save_npy`).
- **Active Network State**:
  - When `HYDROPULSE_PERSIST_ACTIVE_STATE=true` (default in `prod`), committing a network import, build, or AI suggestion writes `backend/.state/active_network.inp` and `backend/.state/active_state.json` atomically.
  - On process restart, `HydraulicEngine.boot()` retains the baseline Kengeri DMA in `_baseline_snapshot` (so `POST /api/network/reset` with `target="baseline"` always works) and automatically restores the persisted active network revision.
  - Rolling back to `"baseline"` cleans up `backend/.state/active_network.inp` and `active_state.json`.
- **Ephemeral In-Memory State**: Preview tokens (`_preview_cache`, TTL 30 min, max 32 entries) and IoT telemetry ring buffers (`max 100 packets/sensor`) are in-memory and reset on process restart.

---

## 10. Measured Performance & Resource Profile

Measured on the committed Kengeri DMA network (`129 junctions`, `168 pipes`, `1 reservoir`, `5 sentinel sensors`) on Linux `x86_64` (CPython 3.11.16, WNTR 1.5.0):

| Metric | Measured Value | Target / Budget |
| :--- | :--- | :--- |
| **Warm Startup (cached sensitivity matrix)** | **371.0 ms** | `< 2,000 ms` |
| **Cold Startup (5x129 sensitivity curve build)** | **10.52 s** | `< 60 s` |
| **`GET /api/health` Latency (p50 / p95)** | **2.11 ms / 5.21 ms** | `< 50 ms` |
| **`GET /api/network` Latency (p50 / p95)** | **3.77 ms / 5.09 ms** | `< 100 ms` |
| **`POST /api/simulate-leak` Latency (p50 / p95)** | **246.3 ms / 303.7 ms** | `< 1,000 ms` (SOT §12) |
| **`POST /api/what-if` Latency (p50 / p95)** | **135.7 ms / 263.5 ms** | `< 1,000 ms` |
| **`GET /api/iot/state` Latency (p50)** | **1.43 ms** | `< 50 ms` |
| **Peak Resident Memory (RSS)** | **~611 MB** | `< 1,024 MB` |

> **Note on Network Provenance**: The committed `backend/bangalore_kengeri.inp` (`network_name: "kengeri_synthetic"`, `129 junctions`, `168 pipes`) was generated inside the Kengeri bounding box (`[12.9000, 12.9180] N, [77.4750, 77.4950] E`) with calibrated synthetic topography (`805–820 m ASL`). To regenerate from live OpenStreetMap Overpass data on a machine with unrestricted Overpass access, run `.venv/bin/python backend/generate_bangalore_dma.py --source osm` followed by `make presets`.
