"""HydroPulse FastAPI server — API CONTRACT v1.0 (SOURCE_OF_TRUTH.md §7).

Run from the repo root:  uvicorn backend.main:app --host 0.0.0.0 --port 8000
Endpoints are sync `def` (FastAPI runs them in a threadpool); the engine
serialises every WNTR solve behind its own lock.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from backend import ai_advisor, iot_manager, network_builder, scenario_engine
from backend.config import BackendConfig, load_config
from backend.hydraulic_engine import HydraulicEngine
from backend.openrouter_client import get_openrouter_client
from backend.security import (
    SecurityAndTracingMiddleware,
    make_admin_dependency,
    register_exception_handlers,
)
from data_utils.schemas import (
    AiSuggestionsResponse,
    ApplySuggestionRequest,
    ApplySuggestionResponse,
    AutoValveRequest,
    AutoValveResponse,
    HealthResponse,
    IntelliSenseRequest,
    IntelliSenseResponse,
    IoTStateResponse,
    IsolationRequest,
    IsolationResponse,
    LeakSimulationRequest,
    LeakSimulationResponse,
    NetworkBuildCommitRequest,
    NetworkBuildPreviewRequest,
    NetworkBuildPreviewResponse,
    NetworkImportCommitRequest,
    NetworkImportPreviewRequest,
    NetworkImportPreviewResponse,
    NetworkPresetsListResponse,
    NetworkResetRequest,
    NetworkResponse,
    NetworkRevisionCommitResponse,
    PipeConditionResponse,
    Preset,
    PresetsResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    WhatIfRequest,
    WhatIfResponse,
)
config: BackendConfig = load_config()
logging.basicConfig(
    level=getattr(logging, config.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("hydropulse.api")

INP_PATH = str(config.inp_path)
META_PATH = str(config.meta_path)
PRESETS_PATH = Path(config.presets_path)

engine: HydraulicEngine | None = None


def get_config() -> BackendConfig:
    return config


def reload_runtime_config(environ: dict[str, str] | None = None) -> BackendConfig:
    """Reload configuration from environment (used during startup and tests)."""
    global config, INP_PATH, META_PATH, PRESETS_PATH
    config = load_config(environ)
    INP_PATH = str(config.inp_path)
    META_PATH = str(config.meta_path)
    PRESETS_PATH = Path(config.presets_path)
    get_openrouter_client().reload_config(config)
    return config


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global engine
    cfg = reload_runtime_config()
    # Update CORS middleware origins dynamically for active environment config
    for mw in app_instance.user_middleware:
        if mw.cls is CORSMiddleware:
            mw.kwargs["allow_origins"] = list(cfg.cors_origins)
    app_instance.middleware_stack = app_instance.build_middleware_stack()
    cache_file = (
        cfg.cache_dir / "sensitivity_matrix_net3.npy"
        if not Path(INP_PATH).exists()
        else cfg.cache_dir / "sensitivity_matrix.npy"
    )
    engine = HydraulicEngine(
        INP_PATH,
        META_PATH,
        cache_path=str(cache_file),
        state_dir=cfg.state_dir,
        persist_active_state=cfg.persist_active_state,
    ).boot()
    log.info(
        "boot ready: env=%s network=%s rev=%s junctions=%d sensors=%s openrouter_active=%s",
        cfg.env,
        engine.network_name,
        engine.network_revision,
        len(engine.junctions),
        engine.sensor_ids,
        cfg.openrouter_ready,
    )
    yield
    log.info("shutting down HydroPulse API cleanly")


app = FastAPI(
    title="HydroPulse API",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs" if config.docs_enabled else None,
    redoc_url="/redoc" if config.docs_enabled else None,
    openapi_url="/openapi.json" if config.docs_enabled else None,
)
app.add_middleware(SecurityAndTracingMiddleware, get_config=get_config)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
register_exception_handlers(app, get_config=get_config)
require_admin = make_admin_dependency(get_config)


def _engine() -> HydraulicEngine:
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    return engine

def _require_junction(eng: HydraulicEngine, node_id: str) -> None:
    """Contract 404 for anything that is not a junction (reservoirs/tanks included)."""
    if node_id not in eng.junctions:
        raise HTTPException(status_code=404, detail=f"Unknown junction id: {node_id}")


@app.get("/api/health", response_model=HealthResponse)
def health() -> dict:
    return _engine().health()


@app.get("/api/ready")
def ready() -> dict:
    """Readiness probe distinguishing process liveness from full hydraulic twin & matrix readiness."""
    eng = _engine()
    h = eng.health()
    if not h.get("matrix_ready") or not eng.base_pressure:
        raise HTTPException(status_code=503, detail="Hydraulic engine sensitivity matrix or baseline not ready")
    return {
        "status": "ready",
        "network_name": h.get("network_name"),
        "network_revision": h.get("network_revision"),
        "num_junctions": h.get("num_junctions"),
        "num_nodes": h.get("num_junctions"),
        "num_pipes": h.get("num_pipes"),
        "matrix_ready": h.get("matrix_ready"),
        "source": h.get("source"),
        "openrouter_active": get_config().openrouter_ready,
    }

@app.get("/api/network", response_model=NetworkResponse)
def network() -> dict:
    return _engine().get_network_payload()


@app.get("/api/presets", response_model=PresetsResponse)
def presets() -> dict:
    """data_utils/presets.json filtered to ids in the loaded network; auto-generated if none remain."""
    eng = _engine()
    valid: list[dict] = []
    if PRESETS_PATH.exists():
        try:
            raw = json.loads(PRESETS_PATH.read_text()).get("presets", [])
            for p in raw:
                preset = Preset.model_validate(p)
                if preset.target_node_id in eng.junctions:
                    valid.append(preset.model_dump())
                else:
                    log.warning("dropping preset %s: unknown node %s", preset.id, preset.target_node_id)
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable %s: %s", PRESETS_PATH, exc)
    return {"presets": valid if len(valid) == 3 else eng.auto_presets()}


@app.post("/api/simulate-leak", response_model=LeakSimulationResponse)
def simulate_leak(req: LeakSimulationRequest) -> dict:
    eng = _engine()
    _require_junction(eng, req.target_node_id)
    try:
        out = eng.simulate_and_triangulate(req.target_node_id, req.leak_area_cm2, req.noise_pct, req.seed)
    except Exception as exc:  # solver failure → contract 500 shape
        log.exception("simulation failed")
        raise HTTPException(status_code=500, detail=f"Simulation failed: {exc}")
    log.info("simulate-leak node=%s area=%.1f cm2 noise=%.1f%% top=%s %.0f ms",
             req.target_node_id, req.leak_area_cm2, req.noise_pct,
             out["predicted_top_candidates"][0]["node_id"], out["compute_time_ms"])
    return out


@app.post("/api/isolate", response_model=IsolationResponse)
def isolate(req: IsolationRequest) -> dict:
    """(stretch) Close valves with the leak present and report the resulting pressures."""
    eng = _engine()
    _require_junction(eng, req.target_node_id)
    unknown = [v for v in req.valve_ids if v not in eng.wn.link_name_list]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown pipe id: {unknown[0]}")
    try:
        return eng.simulate_isolation(req.valve_ids, req.target_node_id, req.leak_area_cm2)
    except Exception as exc:
        log.exception("isolation failed")
        raise HTTPException(status_code=500, detail=f"Simulation failed: {exc}")


# ═══════════════════════════ PART 2 (v1.1) ENDPOINTS ═══════════════════════════


@app.post("/api/what-if", response_model=WhatIfResponse)
def what_if(req: WhatIfRequest) -> WhatIfResponse:
    """Evaluate a counterfactual scenario on an isolated network clone."""
    return scenario_engine.evaluate_what_if(_engine(), req)


@app.get("/api/ai/suggestions", response_model=AiSuggestionsResponse)
def get_ai_suggestions() -> AiSuggestionsResponse:
    """Return simulation-validated structural and sensor placement recommendations."""
    return ai_advisor.get_suggestions(_engine())


@app.post("/api/ai/apply-suggestion", response_model=ApplySuggestionResponse, dependencies=[Depends(require_admin)])
def apply_ai_suggestion(req: ApplySuggestionRequest) -> ApplySuggestionResponse:
    """Apply a confirmed AI recommendation and promote a new network revision."""
    return ai_advisor.apply_suggestion(_engine(), req)

@app.get("/api/network/presets", response_model=NetworkPresetsListResponse)
def list_network_presets() -> NetworkPresetsListResponse:
    """Catalogue of available and planned ward/DMA network topologies."""
    return network_builder.list_network_presets(_engine())


@app.post("/api/network/import/preview", response_model=NetworkImportPreviewResponse)
def import_preview(req: NetworkImportPreviewRequest) -> NetworkImportPreviewResponse:
    """Parse and validate an EPANET .inp, GeoJSON, or preset without mutating active state."""
    return network_builder.preview_import(_engine(), req)


@app.post("/api/network/import/commit", response_model=NetworkRevisionCommitResponse, dependencies=[Depends(require_admin)])
def import_commit(req: NetworkImportCommitRequest) -> NetworkRevisionCommitResponse:
    """Activate a validated network import preview after explicit user confirmation."""
    return network_builder.commit_import(_engine(), req)


@app.post("/api/network/import", response_model=NetworkImportPreviewResponse, dependencies=[Depends(require_admin)])
def import_legacy_preview(req: NetworkImportPreviewRequest) -> NetworkImportPreviewResponse:
    """Preview-only alias for /api/network/import/preview (requires explicit commit token to activate)."""
    return network_builder.preview_import(_engine(), req)

@app.post("/api/network/intellisense", response_model=IntelliSenseResponse)
def network_intellisense(req: IntelliSenseRequest) -> IntelliSenseResponse:
    """Non-mutating hydraulic IntelliSense for a proposed connection."""
    return network_builder.compute_intellisense(_engine(), req)


@app.post("/api/network/build/preview", response_model=NetworkBuildPreviewResponse)
def build_preview(req: NetworkBuildPreviewRequest) -> NetworkBuildPreviewResponse:
    """Validate proposed junctions/pipes on a cloned network and return before/after metrics."""
    return network_builder.preview_build(_engine(), req)


@app.post("/api/network/build/commit", response_model=NetworkRevisionCommitResponse, dependencies=[Depends(require_admin)])
def build_commit(req: NetworkBuildCommitRequest) -> NetworkRevisionCommitResponse:
    """Promote a validated map-builder preview to a new network revision."""
    return network_builder.commit_build(_engine(), req)


@app.post("/api/network/reset", response_model=NetworkRevisionCommitResponse, dependencies=[Depends(require_admin)])
def reset_network(req: NetworkResetRequest) -> NetworkRevisionCommitResponse:
    """Restore the previous or baseline network revision."""
    return network_builder.reset_network(_engine(), req)

@app.get("/api/iot/state", response_model=IoTStateResponse)
def get_iot_state(
    request: Request,
    demo_tick: bool = Query(False, description="Generate one deterministic simulated telemetry packet tick"),
    seed: int | None = Query(None, ge=0, description="Optional deterministic seed for simulated demo tick"),
) -> IoTStateResponse:
    """Return simulated/ingested IoT telemetry buffer and auto-valve state."""
    cfg = get_config()
    allow_demo_tick = bool(demo_tick)
    if allow_demo_tick and cfg.is_prod and cfg.require_admin_auth:
        # In production with admin auth enabled, only allow state-mutating demo_tick if valid admin token is present
        import hmac
        provided = (request.headers.get("X-Admin-Token") or "").strip()
        if not provided and request.headers.get("Authorization", "").lower().startswith("bearer "):
            provided = request.headers.get("Authorization", "")[7:].strip()
        if not (cfg.admin_token and provided and hmac.compare_digest(provided.encode("utf-8"), cfg.admin_token.encode("utf-8"))):
            allow_demo_tick = False
    return iot_manager.get_state(_engine(), generate_demo_tick=allow_demo_tick, seed=seed)

@app.post("/api/iot/ingest", response_model=TelemetryIngestResponse, dependencies=[Depends(require_admin)])
def ingest_iot_telemetry(req: TelemetryIngestRequest) -> TelemetryIngestResponse:
    """Validate and ingest telemetry packets into the bounded rolling buffer."""
    return iot_manager.ingest(_engine(), req)


@app.get("/api/pipes/condition", response_model=PipeConditionResponse)
def get_pipe_condition() -> PipeConditionResponse:
    """Return pipe condition / RUL transparency report across all network pipes."""
    return iot_manager.get_pipe_conditions(_engine())


@app.post("/api/iot/auto-valve", response_model=AutoValveResponse, dependencies=[Depends(require_admin)])
def iot_auto_valve(req: AutoValveRequest) -> AutoValveResponse:
    """Human-in-the-loop, simulation-only automated valve isolation workflow."""
    return iot_manager.handle_auto_valve(_engine(), req)
