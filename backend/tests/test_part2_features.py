"""HydroPulse v1.1 Part 2 Test Suite (Phase 3).
Thorough tests for all Part 2 features and system invariants:
1. Baseline & Revision Invariants
2. What-If Scenario Engine (POST /api/what-if) & Immutability
3. AI Structural & Sensor Advisor (GET /api/ai/suggestions, POST /api/ai/apply-suggestion)
4. Network Importer, Map Builder & IntelliSense
5. Simulated IoT Telemetry, Pipe Condition / RUL & Safe Auto-Valve
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from data_utils.schemas import (
    AiSuggestionsResponse,
    ApplySuggestionResponse,
    AutoValveResponse,
    HealthResponse,
    IntelliSenseResponse,
    IoTStateResponse,
    LeakSimulationResponse,
    NetworkBuildPreviewResponse,
    NetworkImportPreviewResponse,
    NetworkPresetsListResponse,
    NetworkResetRequest,
    NetworkResponse,
    NetworkRevisionCommitResponse,
    PipeConditionResponse,
    TelemetryIngestResponse,
    WhatIfResponse,
)

REVISION_REGEX = r"^rev-\d+-[0-9a-f]{8}$"


@pytest.fixture(scope="module")
def client():
    """Boot FastAPI app in a test client for Part 2 tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_network_state(client):
    """Ensure baseline state before and after each test."""
    yield
    try:
        h = client.get("/api/health").json()
        if h.get("can_rollback", False) or not h.get("network_revision", "").startswith("rev-1-"):
            client.post("/api/network/reset", json={"target": "baseline", "confirm": True})
        client.post("/api/iot/auto-valve", json={"action": "reset_valves"})
        client.post("/api/iot/auto-valve", json={"action": "set_mode", "enable_auto_valve": False})
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# 1. Baseline & Revision Invariants
# ═════════════════════════════════════════════════════════════════════════════

def test_health_endpoint_revision_and_v1_0_invariants(client):
    """Verify /api/health includes valid network_revision, source, can_rollback and 100% of v1.0 fields."""
    r = client.get("/api/health")
    assert r.status_code == 200, f"Health check failed: {r.text}"
    data = r.json()

    # Validate against HealthResponse schema
    model = HealthResponse.model_validate(data)

    # 100% of v1.0 fields preserved
    assert model.status == "ok"
    assert len(model.network_name) > 0
    assert model.num_junctions > 0
    assert model.num_pipes > 0
    assert len(model.sensor_ids) == 5
    assert isinstance(model.matrix_ready, bool)
    assert model.matrix_ready is True

    # v1.1 additions
    assert model.network_revision is not None
    assert re.match(REVISION_REGEX, model.network_revision), f"Malformed revision: {model.network_revision}"
    assert model.source in ["synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback"]
    assert model.can_rollback is False  # baseline network cannot rollback


def test_network_endpoint_revision_and_v1_0_invariants(client):
    """Verify /api/network includes network_revision, revision_meta and preserves all v1.0 nodes and pipes."""
    r = client.get("/api/network")
    assert r.status_code == 200, f"Network query failed: {r.text}"
    data = r.json()

    # Validate against NetworkResponse schema
    model = NetworkResponse.model_validate(data)

    # 100% of v1.0 fields preserved
    assert len(model.network_name) > 0
    assert 12.0 <= model.center.lat <= 14.0
    assert 76.0 <= model.center.lng <= 79.0
    assert len(model.nodes) > 0
    assert len(model.pipes) > 0
    assert len(model.sensor_ids) == 5

    # Node attributes
    junction_count = sum(1 for n in model.nodes if n.type == "junction")
    source_count = sum(1 for n in model.nodes if n.type in ("reservoir", "tank"))
    assert junction_count > 0
    assert source_count > 0
    for node in model.nodes:
        assert 12.0 <= node.lat <= 14.0
        assert 76.0 <= node.lng <= 79.0
        assert node.leak_probability == 0.0

    # Pipe attributes
    for pipe in model.pipes:
        assert pipe.diameter_mm > 0.0
        assert pipe.length_m > 0.0
        assert pipe.status in ["OPEN", "CLOSED"]

    # v1.1 revision metadata additions
    assert model.network_revision is not None
    assert re.match(REVISION_REGEX, model.network_revision)
    meta = model.revision_meta
    assert meta is not None
    assert meta.network_revision == model.network_revision
    assert meta.crs == "EPSG:4326"
    assert meta.num_nodes == len(model.nodes)
    assert meta.num_pipes == len(model.pipes)
    assert meta.can_rollback is False
    assert isinstance(meta.validation_summary, dict)


# ═════════════════════════════════════════════════════════════════════════════
# 2. What-If Scenario Engine (POST /api/what-if)
# ═════════════════════════════════════════════════════════════════════════════

def test_what_if_noop_scenario_matches_baseline_tolerances(client):
    """Empty/no-op scenario matches baseline pressures within solver tolerance (max(abs(delta)) < 1e-3)."""
    r = client.post("/api/what-if", json={})
    assert r.status_code == 200, f"What-if no-op failed: {r.text}"
    data = r.json()
    model = WhatIfResponse.model_validate(data)

    assert model.converged is True
    assert len(model.solver_message) > 0
    assert re.match(REVISION_REGEX, model.network_revision)

    # Pressures delta should be negligible (< 1e-3 m across all junctions)
    assert len(model.node_pressure_deltas_m) > 0
    max_delta = max(abs(v) for v in model.node_pressure_deltas_m.values())
    assert max_delta < 1e-3, f"Expected no-op delta < 1e-3, got {max_delta}"

    # Baseline and scenario metrics should be virtually identical
    assert abs(model.baseline.min_pressure_m - model.scenario.min_pressure_m) < 1e-3
    assert abs(model.baseline.avg_pressure_m - model.scenario.avg_pressure_m) < 1e-3
    assert abs(model.baseline.max_pressure_m - model.scenario.max_pressure_m) < 1e-3
    assert model.baseline.pressure_deficit_nodes == model.scenario.pressure_deficit_nodes
    assert model.baseline.affected_households == model.scenario.affected_households
    assert len(model.limitations) >= 3


def test_what_if_pipe_overrides_demand_multiplier_and_leaks(client):
    """Pipe overrides (CLOSED, diameter/roughness), demand multiplier, reservoir head, and leaks work."""
    net = client.get("/api/network").json()
    pipe0 = net["pipes"][0]
    junction0 = [n["id"] for n in net["nodes"] if n["type"] == "junction"][0]
    junction1 = [n["id"] for n in net["nodes"] if n["type"] == "junction"][1]

    payload = {
        "pipe_overrides": [
            {
                "pipe_id": pipe0["id"],
                "status": "CLOSED",
                "diameter_mm": 200.0,
                "roughness": 110.0,
            }
        ],
        "demand_multiplier": 1.2,
        "reservoir_head_delta_m": -5.0,
        "leaks": [
            {"node_id": junction0, "leak_area_cm2": 25.0},
            {"node_id": junction1, "leak_area_cm2": 15.0},
        ],
        "seed": 42,
    }

    r = client.post("/api/what-if", json=payload)
    assert r.status_code == 200, f"What-if scenario failed: {r.text}"
    model = WhatIfResponse.model_validate(r.json())

    assert model.converged is True
    assert model.scenario.total_leak_loss_lpm > 0.0
    assert model.scenario.tankers_lost_per_day > 0.0

    # Closing pipe0 must generate a closed pipe warning
    pipe_warning_ids = [w.pipe_id for w in model.pipe_warnings]
    assert pipe0["id"] in pipe_warning_ids

    # Limitations disclaimer must be present
    assert any("Steady-state PDD" in lim for lim in model.limitations)
    assert any("Counterfactual scenario" in lim for lim in model.limitations)


def test_what_if_execution_never_mutates_active_baseline(client):
    """Closing pipes or injecting leaks in /api/what-if NEVER mutates active baseline."""
    # Step 1: Capture baseline network state and fixed-seed leak simulation
    net_before = client.get("/api/network").json()
    pipe0 = net_before["pipes"][0]
    rev_before = net_before["network_revision"]

    sim_before_r = client.post(
        "/api/simulate-leak",
        json={"target_node_id": "J15", "leak_area_cm2": 25.0, "noise_pct": 1.5, "seed": 42},
    )
    assert sim_before_r.status_code == 200
    sim_before = sim_before_r.json()

    # Step 2: Run aggressive what-if scenario (close pipe, inject leak, elevate demand)
    what_if_r = client.post(
        "/api/what-if",
        json={
            "pipe_overrides": [{"pipe_id": pipe0["id"], "status": "CLOSED"}],
            "demand_multiplier": 1.5,
            "reservoir_head_delta_m": -10.0,
            "leaks": [{"node_id": "J15", "leak_area_cm2": 50.0}],
            "seed": 42,
        },
    )
    assert what_if_r.status_code == 200

    # Step 3: Verify active network state and subsequent simulate-leak results are identical
    net_after = client.get("/api/network").json()
    assert net_after["network_revision"] == rev_before, "What-If bumped active network_revision!"
    pipe0_after = [p for p in net_after["pipes"] if p["id"] == pipe0["id"]][0]
    assert pipe0_after["status"] == pipe0["status"] == "OPEN", "Active network pipe was permanently closed!"

    sim_after_r = client.post(
        "/api/simulate-leak",
        json={"target_node_id": "J15", "leak_area_cm2": 25.0, "noise_pct": 1.5, "seed": 42},
    )
    assert sim_after_r.status_code == 200
    sim_after = sim_after_r.json()

    # Confirm bitwise-identical simulation results
    for field in ("actual_loss_lpm", "predicted_top_candidates", "sensor_readings", "node_heatmap"):
        assert sim_before[field] == sim_after[field], f"Baseline simulate-leak drifted on field '{field}'!"


def test_what_if_validation_gates(client):
    """Validation gates: unknown pipe -> 404, unknown leak -> 404, >max leaks -> 422, bad demand -> 422, stale rev -> 409."""
    rev = client.get("/api/health").json()["network_revision"]

    # Unknown pipe ID -> 404
    r_pipe_404 = client.post("/api/what-if", json={"pipe_overrides": [{"pipe_id": "P_DOES_NOT_EXIST"}]})
    assert r_pipe_404.status_code == 404
    assert "Unknown pipe_id" in r_pipe_404.json()["detail"]

    # Unknown leak junction ID -> 404
    r_leak_404 = client.post(
        "/api/what-if", json={"leaks": [{"node_id": "J_DOES_NOT_EXIST", "leak_area_cm2": 25.0}]}
    )
    assert r_leak_404.status_code == 404
    assert "Unknown junction node_id" in r_leak_404.json()["detail"]

    # >3 simultaneous leaks (schema limit max_length=5, 6 leaks -> 422)
    r_leaks_422 = client.post(
        "/api/what-if",
        json={"leaks": [{"node_id": "J1", "leak_area_cm2": 25.0}] * 6},
    )
    assert r_leaks_422.status_code == 422

    # Out-of-range demand_multiplier (0.1 and 5.0 -> 422)
    assert client.post("/api/what-if", json={"demand_multiplier": 0.1}).status_code == 422
    assert client.post("/api/what-if", json={"demand_multiplier": 5.0}).status_code == 422

    # Stale expected_network_revision -> 409
    r_stale_409 = client.post("/api/what-if", json={"expected_network_revision": "rev-999-stale"})
    assert r_stale_409.status_code == 409
    assert "Stale network_revision" in r_stale_409.json()["detail"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. AI Structural & Sensor Advisor
# ═════════════════════════════════════════════════════════════════════════════

def test_ai_suggestions_endpoint_and_categories(client):
    """GET /api/ai/suggestions returns 3+ suggestions across categories with benchmark metrics."""
    r = client.get("/api/ai/suggestions")
    assert r.status_code == 200, f"AI suggestions failed: {r.text}"
    model = AiSuggestionsResponse.model_validate(r.json())

    assert re.match(REVISION_REGEX, model.network_revision)
    assert len(model.suggestions) >= 3, f"Expected 3+ suggestions, got {len(model.suggestions)}"

    categories = {s.category for s in model.suggestions}
    assert "loop_completion" in categories
    assert "pipe_upsizing" in categories
    assert "sensor_placement" in categories

    for s in model.suggestions:
        assert s.suggestion_id.startswith("sug-")
        assert s.source_network_revision == model.network_revision
        assert isinstance(s.baseline_metrics, dict)
        assert isinstance(s.simulated_after_metrics, dict)
        assert len(s.uncertainty_note) > 0

    # Sensor placement benchmark summary verification
    bench = model.sensor_benchmark
    assert isinstance(bench, dict)
    assert bench.get("eval_sample_count", 0) > 0
    assert bench.get("seed") == 42
    assert bench.get("noise_pct") == 1.5
    assert len(bench.get("current_sensor_ids", [])) == 5
    assert len(bench.get("candidate_sensor_ids", [])) == 5
    assert "candidate_top1_pct" in bench
    assert "candidate_mean_error_m" in bench


def test_ai_apply_suggestion_validation_gates(client):
    """POST /api/ai/apply-suggestion validation gates: confirm=False -> 422, unknown ID -> 404, stale rev -> 409."""
    sugs = client.get("/api/ai/suggestions").json()
    active_rev = sugs["network_revision"]
    target_sug_id = sugs["suggestions"][0]["suggestion_id"]

    # confirm=False -> 422
    r_unconfirmed = client.post(
        "/api/ai/apply-suggestion",
        json={"suggestion_id": target_sug_id, "expected_network_revision": active_rev, "confirm": False},
    )
    assert r_unconfirmed.status_code == 422

    # Unknown suggestion_id -> 404
    r_unknown_id = client.post(
        "/api/ai/apply-suggestion",
        json={"suggestion_id": "sug-nonexistent-id", "expected_network_revision": active_rev, "confirm": True},
    )
    assert r_unknown_id.status_code == 404

    # Stale expected_network_revision -> 409
    r_stale_rev = client.post(
        "/api/ai/apply-suggestion",
        json={"suggestion_id": target_sug_id, "expected_network_revision": "rev-999-stale", "confirm": True},
    )
    assert r_stale_rev.status_code == 409


def test_ai_apply_suggestion_and_reset_lifecycle(client):
    """Applying valid suggestion promotes network_revision to rev-2-..., and reset cleanly restores baseline."""
    sugs = client.get("/api/ai/suggestions").json()
    baseline_rev = sugs["network_revision"]
    assert baseline_rev.startswith("rev-1-")

    # Apply the first loop completion or upsizing suggestion
    target_sug = sugs["suggestions"][0]
    apply_r = client.post(
        "/api/ai/apply-suggestion",
        json={
            "suggestion_id": target_sug["suggestion_id"],
            "expected_network_revision": baseline_rev,
            "confirm": True,
        },
    )
    assert apply_r.status_code == 200, f"Apply failed: {apply_r.text}"
    apply_model = ApplySuggestionResponse.model_validate(apply_r.json())
    assert apply_model.applied is True
    assert apply_model.previous_network_revision == baseline_rev
    assert apply_model.new_network_revision.startswith("rev-2-")

    # Health must reflect new revision and can_rollback=True
    h_promoted = client.get("/api/health").json()
    assert h_promoted["network_revision"] == apply_model.new_network_revision
    assert h_promoted["can_rollback"] is True

    # Reset back to baseline
    reset_r = client.post("/api/network/reset", json={"target": "baseline", "confirm": True})
    assert reset_r.status_code == 200, f"Reset failed: {reset_r.text}"
    reset_model = NetworkRevisionCommitResponse.model_validate(reset_r.json())
    assert reset_model.committed is True
    assert reset_model.new_network_revision == baseline_rev

    # Health must reflect restored baseline and can_rollback=False
    h_restored = client.get("/api/health").json()
    assert h_restored["network_revision"] == baseline_rev
    assert h_restored["can_rollback"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 4. Network Importer, Map Builder & IntelliSense
# ═════════════════════════════════════════════════════════════════════════════

def test_network_presets_catalog(client):
    """GET /api/network/presets lists kengeri_baseline (available=True) and planned DMAs (available=False)."""
    r = client.get("/api/network/presets")
    assert r.status_code == 200, f"Presets catalogue failed: {r.text}"
    model = NetworkPresetsListResponse.model_validate(r.json())

    preset_map = {p.preset_id: p for p in model.presets}
    assert "kengeri_baseline" in preset_map
    assert preset_map["kengeri_baseline"].available is True
    assert preset_map["kengeri_baseline"].source_type == "synthetic"

    assert "jayanagar_dma" in preset_map
    assert preset_map["jayanagar_dma"].available is False

    assert "indiranagar_dma" in preset_map
    assert preset_map["indiranagar_dma"].available is False


def test_network_intellisense(client):
    """POST /api/network/intellisense returns hydraulic recommendations, geodesic length, velocity, and loop candidates."""
    rev = client.get("/api/health").json()["network_revision"]
    net = client.get("/api/network").json()
    j0 = [n for n in net["nodes"] if n["type"] == "junction"][0]["id"]
    j1 = [n for n in net["nodes"] if n["type"] == "junction"][1]["id"]

    r = client.post(
        "/api/network/intellisense",
        json={"from_node_id": j0, "to_node_id": j1, "expected_network_revision": rev},
    )
    assert r.status_code == 200, f"IntelliSense failed: {r.text}"
    model = IntelliSenseResponse.model_validate(r.json())

    assert model.network_revision == rev
    assert model.length_m > 0.0
    assert model.recommended_diameter_mm in [110, 150, 200, 250, 300, 400]
    assert model.predicted_velocity_ms >= 0.0
    assert model.predicted_headloss_m >= 0.0
    assert isinstance(model.loop_candidates, list)

    # Validation: unknown from_node_id -> 404
    r_unk = client.post("/api/network/intellisense", json={"from_node_id": "J_NONEXISTENT"})
    assert r_unk.status_code == 404

    # Validation: stale expected revision -> 409
    r_stale = client.post(
        "/api/network/intellisense",
        json={"from_node_id": j0, "expected_network_revision": "rev-999-stale"},
    )
    assert r_stale.status_code == 409


def test_network_import_preview_validation_and_safety(client):
    """POST /api/network/import/preview and /api/network/import validate CRS, path safety without mutating active state."""
    rev_before = client.get("/api/health").json()["network_revision"]

    # 1. Path traversal filename -> valid=False
    r_traversal = client.post(
        "/api/network/import/preview",
        json={
            "format": "inp",
            "filename": "../evil.inp",
            "content": "[JUNCTIONS]\nJ1 0 0\n[COORDINATES]\nJ1 77.5 12.9\n[END]",
        },
    )
    assert r_traversal.status_code == 200
    res1 = NetworkImportPreviewResponse.model_validate(r_traversal.json())
    assert res1.valid is False
    assert any("path traversal" in err.lower() for err in res1.errors)

    # 2. Unsupported Non-WGS84 CRS (EPSG:3857) -> valid=False
    bad_crs_geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [],
    }
    r_crs = client.post(
        "/api/network/import/preview",
        json={"format": "geojson", "filename": "mercator.geojson", "content": json.dumps(bad_crs_geojson)},
    )
    assert r_crs.status_code == 200
    res2 = NetworkImportPreviewResponse.model_validate(r_crs.json())
    assert res2.valid is False
    assert any("unsupported crs" in err.lower() for err in res2.errors)

    # 3. Out-of-bounds coordinates -> valid=False
    oob_geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}, "properties": {"id": "J1"}}
        ],
    }
    r_oob = client.post(
        "/api/network/import/preview",
        json={"format": "geojson", "filename": "oob.geojson", "content": json.dumps(oob_geojson)},
    )
    assert r_oob.status_code == 200
    res3 = NetworkImportPreviewResponse.model_validate(r_oob.json())
    assert res3.valid is False
    assert any("outside bengaluru bounds" in err.lower() for err in res3.errors)

    # 4. Malformed JSON -> valid=False
    r_malformed = client.post(
        "/api/network/import/preview",
        json={"format": "geojson", "filename": "broken.geojson", "content": "not-valid-json"},
    )
    assert r_malformed.status_code == 200
    assert r_malformed.json()["valid"] is False

    # 5. Legacy alias /api/network/import
    r_alias = client.post(
        "/api/network/import",
        json={"format": "inp", "filename": "../evil.inp", "content": "dummy-content"},
    )
    assert r_alias.status_code == 200
    assert r_alias.json()["valid"] is False

    # Active network revision must remain completely untouched
    rev_after = client.get("/api/health").json()["network_revision"]
    assert rev_before == rev_after


def test_network_build_preview_commit_and_reset_lifecycle(client):
    """Full builder lifecycle: preview proposed elements, commit (rev advances), verify rollback via reset."""
    h0 = client.get("/api/health").json()
    rev0 = h0["network_revision"]
    nodes0 = h0["num_junctions"]
    pipes0 = h0["num_pipes"]

    # Preview build: add a new junction and a pipe connecting J1 to the new junction
    preview_r = client.post(
        "/api/network/build/preview",
        json={
            "new_nodes": [
                {
                    "id": "J_TEST_BUILD_NODE",
                    "lat": 12.915,
                    "lng": 77.495,
                    "elevation_m": 810.0,
                    "base_demand_lps": 0.2,
                }
            ],
            "new_pipes": [
                {
                    "id": "P_TEST_BUILD_PIPE",
                    "from_node": "J1",
                    "to_node": "J_TEST_BUILD_NODE",
                    "diameter_mm": 150.0,
                    "length_m": 120.0,
                    "roughness": 130.0,
                }
            ],
            "expected_network_revision": rev0,
        },
    )
    assert preview_r.status_code == 200, f"Build preview failed: {preview_r.text}"
    prev_model = NetworkBuildPreviewResponse.model_validate(preview_r.json())
    assert prev_model.valid is True
    assert prev_model.preview_token is not None
    token = prev_model.preview_token

    # Commit build
    commit_r = client.post(
        "/api/network/build/commit",
        json={"preview_token": token, "expected_network_revision": rev0, "confirm": True},
    )
    assert commit_r.status_code == 200, f"Build commit failed: {commit_r.text}"
    commit_model = NetworkRevisionCommitResponse.model_validate(commit_r.json())
    assert commit_model.committed is True
    rev1 = commit_model.new_network_revision
    assert rev1.startswith("rev-2-")

    # Confirm network element counts increased
    h1 = client.get("/api/health").json()
    assert h1["num_junctions"] == nodes0 + 1
    assert h1["num_pipes"] == pipes0 + 1
    assert h1["network_revision"] == rev1
    assert h1["can_rollback"] is True

    # Rollback via reset
    reset_r = client.post("/api/network/reset", json={"target": "baseline", "confirm": True})
    assert reset_r.status_code == 200
    reset_model = NetworkRevisionCommitResponse.model_validate(reset_r.json())
    assert reset_model.new_network_revision == rev0

    # Confirm node and pipe counts restored to original baseline
    h2 = client.get("/api/health").json()
    assert h2["num_junctions"] == nodes0
    assert h2["num_pipes"] == pipes0
    assert h2["network_revision"] == rev0
    assert h2["can_rollback"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 5. Simulated IoT Telemetry, Pipe Condition/RUL & Safe Auto-Valve
# ═════════════════════════════════════════════════════════════════════════════

def test_iot_state_demo_tick_deterministic(client):
    """GET /api/iot/state?demo_tick=true&seed=42 generates deterministic SIMULATED packets and auto_valve_enabled=False."""
    r = client.get("/api/iot/state?demo_tick=true&seed=42")
    assert r.status_code == 200, f"IoT state query failed: {r.text}"
    model = IoTStateResponse.model_validate(r.json())

    assert model.auto_valve_enabled is False
    assert model.emergency_stop is False
    assert model.stream_mode in ["SIMULATED", "OFF"]
    assert len(model.latest_readings) > 0

    for packet in model.latest_readings.values():
        assert packet.quality == "SIMULATED"
        assert packet.source_type == "simulator"
        assert packet.network_revision == model.network_revision
        assert packet.measurement_type in ["pressure_m", "flow_m3h", "demand_lps"]


def test_iot_ingest_deduplication_and_validation(client):
    """POST /api/iot/ingest deduplicates repeated sequences and rejects invalid target IDs, units, or stale revs."""
    rev = client.get("/api/health").json()["network_revision"]

    valid_pkt = {
        "source_id": "sensor_ingest_test",
        "source_type": "simulator",
        "network_revision": rev,
        "observed_at_utc": "2026-11-05T12:00:00Z",
        "target_id": "J72",
        "measurement_type": "pressure_m",
        "value": 36.5,
        "unit": "m",
        "sequence_id": 1,
        "quality": "SIMULATED",
    }
    dup_pkt = dict(valid_pkt)  # Same source_id and sequence_id

    unk_target_pkt = dict(valid_pkt)
    unk_target_pkt["source_id"] = "sensor_ingest_bad_tgt"
    unk_target_pkt["target_id"] = "UNKNOWN_JUNCTION_999"
    unk_target_pkt["sequence_id"] = 2

    mismatch_unit_pkt = dict(valid_pkt)
    mismatch_unit_pkt["source_id"] = "sensor_ingest_bad_unit"
    mismatch_unit_pkt["unit"] = "m3h"  # Invalid unit for pressure_m
    mismatch_unit_pkt["sequence_id"] = 3

    stale_rev_pkt = dict(valid_pkt)
    stale_rev_pkt["source_id"] = "sensor_ingest_stale_rev"
    stale_rev_pkt["network_revision"] = "rev-999-bad"
    stale_rev_pkt["sequence_id"] = 4

    payload = {"packets": [valid_pkt, dup_pkt, unk_target_pkt, mismatch_unit_pkt, stale_rev_pkt]}
    r = client.post("/api/iot/ingest", json=payload)
    assert r.status_code == 200, f"Ingest failed: {r.text}"
    model = TelemetryIngestResponse.model_validate(r.json())

    assert model.accepted >= 1
    assert model.deduplicated >= 1
    assert model.rejected >= 1


def test_pipes_condition_report_rul_transparency(client):
    """GET /api/pipes/condition returns honest RUL unavailable, stress index in [0, 1], and populated missing_inputs."""
    r = client.get("/api/pipes/condition")
    assert r.status_code == 200, f"Pipe condition failed: {r.text}"
    model = PipeConditionResponse.model_validate(r.json())

    # RUL is unavailable on synthetic network because structural wall thickness/install age are absent
    assert model.rul_available is False
    assert len(model.methodology_note) > 0
    assert len(model.pipes) > 0

    for p in model.pipes:
        assert p.rul_status == "unavailable"
        assert p.rul_years is None
        assert 0.0 <= p.illustrative_stress_index <= 1.0
        assert len(p.missing_inputs) > 0
        assert "material" in p.missing_inputs or "wall_thickness_mm" in p.missing_inputs
        assert len(p.provenance_note) > 0


def test_safe_auto_valve_state_machine_and_safety_gates(client):
    """Human-in-the-loop auto-valve workflow: refused by default, enable -> propose -> confirm -> estop -> reset."""
    rev = client.get("/api/health").json()["network_revision"]

    # 1. Default: auto_valve_enabled is False; confirm_close must be refused
    r_refused_default = client.post(
        "/api/iot/auto-valve",
        json={"action": "confirm_close", "confirm": True, "expected_network_revision": rev},
    )
    assert r_refused_default.status_code == 200
    res_def = AutoValveResponse.model_validate(r_refused_default.json())
    assert res_def.status == "refused"
    assert res_def.auto_valve_enabled is False

    # 2. Set mode: enable auto-valve
    r_enable = client.post(
        "/api/iot/auto-valve",
        json={"action": "set_mode", "enable_auto_valve": True, "expected_network_revision": rev},
    )
    assert r_enable.status_code == 200
    res_en = AutoValveResponse.model_validate(r_enable.json())
    assert res_en.auto_valve_enabled is True
    assert res_en.status == "idle"

    # 3. Propose valve closure for a leak at J15
    r_prop = client.post(
        "/api/iot/auto-valve",
        json={
            "action": "propose",
            "target_node_id": "J15",
            "leak_area_cm2": 25.0,
            "expected_network_revision": rev,
        },
    )
    assert r_prop.status_code == 200
    res_prop = AutoValveResponse.model_validate(r_prop.json())
    assert res_prop.status == "proposed"
    assert res_prop.proposal is not None
    proposal_id = res_prop.proposal["proposal_id"]
    valves_to_close = res_prop.proposal["valves_to_close"]
    assert len(valves_to_close) > 0

    # 4. Confirm close: transitions to closed_in_simulation
    r_confirm = client.post(
        "/api/iot/auto-valve",
        json={
            "action": "confirm_close",
            "proposal_id": proposal_id,
            "confirm": True,
            "expected_network_revision": rev,
        },
    )
    assert r_confirm.status_code == 200
    res_conf = AutoValveResponse.model_validate(r_confirm.json())
    assert res_conf.status == "closed_in_simulation"
    assert res_conf.simulation_only is True
    assert set(res_conf.active_valve_closures) == set(valves_to_close)

    # 5. Emergency stop: disables auto-valve, clears pending proposals, sets emergency_stop=True
    r_estop = client.post(
        "/api/iot/auto-valve",
        json={"action": "emergency_stop", "expected_network_revision": rev},
    )
    assert r_estop.status_code == 200
    res_estop = AutoValveResponse.model_validate(r_estop.json())
    assert res_estop.status == "stopped"
    assert res_estop.emergency_stop is True
    assert res_estop.auto_valve_enabled is False

    # 6. Verify further closures are refused under emergency stop
    r_blocked_estop = client.post(
        "/api/iot/auto-valve",
        json={"action": "confirm_close", "confirm": True, "expected_network_revision": rev},
    )
    assert r_blocked_estop.status_code == 200
    assert r_blocked_estop.json()["status"] == "refused"

    # 7. Reset valves: clears active closures, resets emergency stop
    r_reset = client.post(
        "/api/iot/auto-valve",
        json={"action": "reset_valves", "expected_network_revision": rev},
    )
    assert r_reset.status_code == 200
    res_reset = AutoValveResponse.model_validate(r_reset.json())
    assert res_reset.status == "reset"
    assert res_reset.active_valve_closures == []
    assert res_reset.emergency_stop is False
