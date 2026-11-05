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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.hydraulic_engine import HydraulicEngine
from data_utils.schemas import (
    HealthResponse,
    IsolationRequest,
    IsolationResponse,
    LeakSimulationRequest,
    LeakSimulationResponse,
    NetworkResponse,
    Preset,
    PresetsResponse,
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


app = FastAPI(title="HydroPulse API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _engine() -> HydraulicEngine:
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    return engine


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
    return {"presets": valid or eng.auto_presets()}


@app.post("/api/simulate-leak", response_model=LeakSimulationResponse)
def simulate_leak(req: LeakSimulationRequest) -> dict:
    eng = _engine()
    try:
        out = eng.simulate_and_triangulate(req.target_node_id, req.leak_area_cm2, req.noise_pct, req.seed)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown junction id: {req.target_node_id}")
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
    try:
        return eng.simulate_isolation(req.valve_ids, req.target_node_id, req.leak_area_cm2)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown junction id: {req.target_node_id}")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pipe id: {exc.args[0]}")
    except Exception as exc:
        log.exception("isolation failed")
        raise HTTPException(status_code=500, detail=f"Simulation failed: {exc}")
