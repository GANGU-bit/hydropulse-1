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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend import ai_advisor, iot_manager, network_builder, scenario_engine
from backend.hydraulic_engine import HydraulicEngine
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hydropulse.api")

INP_PATH = os.environ.get("HYDROPULSE_INP", "backend/bangalore_kengeri.inp")
META_PATH = os.environ.get("HYDROPULSE_META", "backend/network_meta.json")
PRESETS_PATH = Path("data_utils/presets.json")

engine: HydraulicEngine | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    engine = HydraulicEngine(INP_PATH, META_PATH).boot()
    log.info("matrix ready: network=%s junctions=%d sensors=%s",
             engine.network_name, len(engine.junctions), engine.sensor_ids)
    yield


app = FastAPI(title="HydroPulse API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


@app.post("/api/ai/apply-suggestion", response_model=ApplySuggestionResponse)
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


@app.post("/api/network/import/commit", response_model=NetworkRevisionCommitResponse)
def import_commit(req: NetworkImportCommitRequest) -> NetworkRevisionCommitResponse:
    """Activate a validated network import preview after explicit user confirmation."""
    return network_builder.commit_import(_engine(), req)


@app.post("/api/network/import", response_model=NetworkImportPreviewResponse)
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


@app.post("/api/network/build/commit", response_model=NetworkRevisionCommitResponse)
def build_commit(req: NetworkBuildCommitRequest) -> NetworkRevisionCommitResponse:
    """Promote a validated map-builder preview to a new network revision."""
    return network_builder.commit_build(_engine(), req)


@app.post("/api/network/reset", response_model=NetworkRevisionCommitResponse)
def reset_network(req: NetworkResetRequest) -> NetworkRevisionCommitResponse:
    """Restore the previous or baseline network revision."""
    return network_builder.reset_network(_engine(), req)


@app.get("/api/iot/state", response_model=IoTStateResponse)
def get_iot_state(
    demo_tick: bool = Query(False, description="Generate one deterministic simulated telemetry packet tick"),
    seed: int | None = Query(None, ge=0, description="Optional deterministic seed for simulated demo tick"),
) -> IoTStateResponse:
    """Return simulated/ingested IoT telemetry buffer and auto-valve state."""
    return iot_manager.get_state(_engine(), generate_demo_tick=demo_tick, seed=seed)


@app.post("/api/iot/ingest", response_model=TelemetryIngestResponse)
def ingest_iot_telemetry(req: TelemetryIngestRequest) -> TelemetryIngestResponse:
    """Validate and ingest telemetry packets into the bounded rolling buffer."""
    return iot_manager.ingest(_engine(), req)


@app.get("/api/pipes/condition", response_model=PipeConditionResponse)
def get_pipe_condition() -> PipeConditionResponse:
    """Return pipe condition / RUL transparency report across all network pipes."""
    return iot_manager.get_pipe_conditions(_engine())


@app.post("/api/iot/auto-valve", response_model=AutoValveResponse)
def iot_auto_valve(req: AutoValveRequest) -> AutoValveResponse:
    """Human-in-the-loop, simulation-only automated valve isolation workflow."""
    return iot_manager.handle_auto_valve(_engine(), req)
