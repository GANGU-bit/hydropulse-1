"""
API Contract tests for HydroPulse live backend.
Validates live HTTP responses against Pydantic models from data_utils.schemas.
Skips automatically if the backend is not reachable.
"""

import os
import re
import sys
import time
from pathlib import Path
import pytest
import requests
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import (
    AutoValveResponse,
    HealthResponse,
    IntelliSenseResponse,
    IoTStateResponse,
    LeakSimulationResponse,
    NetworkPresetsListResponse,
    NetworkResponse,
    PipeConditionResponse,
    PresetsResponse,
    TelemetryIngestResponse,
    WhatIfResponse,
    lpm_to_tankers_per_day,
)
BASE_URL = os.environ.get("BASE", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {}


def check_api_reachable() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/api/health", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not check_api_reachable(),
    reason=f"Backend API is not reachable at {BASE_URL}"
)


def test_health_endpoint():
    """Verify GET /api/health against HealthResponse schema."""
    url = f"{BASE_URL}/api/health"
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    data = r.json()
    model = HealthResponse.model_validate(data)
    assert model.status == "ok"
    assert len(model.sensor_ids) == 5, f"Expected 5 sensors, got {len(model.sensor_ids)}"
    assert isinstance(model.matrix_ready, bool)
    assert model.num_junctions > 0
    assert model.num_pipes > 0


def test_network_endpoint():
    """Verify GET /api/network against NetworkResponse schema."""
    url = f"{BASE_URL}/api/network"
    r = requests.get(url, timeout=3.0)
    assert r.status_code == 200

    data = r.json()
    model = NetworkResponse.model_validate(data)

    assert len(model.sensor_ids) == 5
    node_ids = {n.id for n in model.nodes}
    sensor_set = set(model.sensor_ids)
    assert sensor_set.issubset(node_ids), "All sensor_ids must exist in nodes"

    sensor_flags = [n.id for n in model.nodes if n.is_sensor]
    assert set(sensor_flags) == sensor_set, "is_sensor flags must match sensor_ids"

    # Verify coordinates
    for n in model.nodes:
        assert 12.0 <= n.lat <= 14.0, f"Lat out of bounds: {n.lat}"
        assert 76.0 <= n.lng <= 79.0, f"Lng out of bounds: {n.lng}"
        assert n.leak_probability == 0.0, "Initial leak_probability in network response must be 0.0"

    # Verify pipes connect valid nodes
    for p in model.pipes:
        assert p.source in node_ids, f"Pipe source {p.source} not in nodes"
        assert p.target in node_ids, f"Pipe target {p.target} not in nodes"
        assert p.diameter_mm > 0
        assert p.length_m > 0
        assert p.status in ["OPEN", "CLOSED"]


def test_presets_endpoint():
    """Verify GET /api/presets against PresetsResponse schema."""
    url = f"{BASE_URL}/api/presets"
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200

    data = r.json()
    model = PresetsResponse.model_validate(data)
    assert len(model.presets) >= 3, "Demo requires at least 3 presets"

    for p in model.presets:
        assert 0.5 <= p.leak_area_cm2 <= 100.0
        assert 0.0 <= p.noise_pct <= 10.0
        assert len(p.target_node_id) > 0


def test_simulate_leak_live_presets():
    """Verify POST /api/simulate-leak for all presets against contract rules."""
    presets_r = requests.get(f"{BASE_URL}/api/presets", timeout=2.0)
    presets = presets_r.json()["presets"]

    net_r = requests.get(f"{BASE_URL}/api/network", timeout=2.0)
    net_data = net_r.json()
    junction_ids = {n["id"] for n in net_data["nodes"] if n["type"] == "junction"}
    sensor_ids_list = net_data["sensor_ids"]

    for p in presets:
        payload = {
            "target_node_id": p["target_node_id"],
            "leak_area_cm2": p["leak_area_cm2"],
            "noise_pct": p["noise_pct"],
            "seed": 42,
        }

        t0 = time.perf_counter()
        r = requests.post(
            f"{BASE_URL}/api/simulate-leak",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert r.status_code == 200, f"Preset {p['id']} failed: {r.text}"
        data = r.json()
        model = LeakSimulationResponse.model_validate(data)

        # Contract requirements
        assert len(model.sensor_readings) == 5, "Must have exactly 5 sensor readings"
        reading_sensor_ids = [sr.sensor_id for sr in model.sensor_readings]
        assert reading_sensor_ids == sensor_ids_list, "Sensor readings must match sensor_ids order"

        # Heatmap keys must equal all junctions
        heatmap_keys = set(model.node_heatmap.keys())
        assert heatmap_keys == junction_ids, "Heatmap must include every junction"
        for j_id, prob in model.node_heatmap.items():
            assert 0.0 <= prob <= 1.0, f"Heatmap probability {prob} out of [0, 1] for {j_id}"
        assert max(model.node_heatmap.values()) == pytest.approx(1.0, abs=1e-3), "Peak heatmap value must be 1.0"

        # Candidates sorted by rank/confidence
        assert 1 <= len(model.predicted_top_candidates) <= 5
        confs = [c.confidence_pct for c in model.predicted_top_candidates]
        assert confs == sorted(confs, reverse=True), "Candidates must be sorted by confidence descending"

        # Tankers formula check: tankers == round(lpm * 0.24, 1) +- 0.2
        for cand in model.predicted_top_candidates:
            expected_tankers = lpm_to_tankers_per_day(cand.estimated_loss_lpm)
            assert abs(cand.tankers_lost_per_day - expected_tankers) <= 0.2

        # Recommended valves sorted
        assert model.recommended_valves_to_close == sorted(model.recommended_valves_to_close)

        # Isolated nodes contains top candidate
        assert model.predicted_top_candidates[0].node_id in model.isolated_node_ids

        # P95 latency target < 1000 ms
        assert model.compute_time_ms < 1000.0, f"Sim compute time {model.compute_time_ms}ms exceeded 1000ms"


def test_simulate_leak_validation_errors():
    """Verify 404 for unknown junction and 422 for invalid inputs."""
    # 404 on unknown node
    r_404 = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "UNKNOWN_NODE_999", "leak_area_cm2": 25.0, "noise_pct": 1.5},
        timeout=2.0,
    )
    assert r_404.status_code == 404, f"Expected 404, got {r_404.status_code}"

    # 422 on leak_area_cm2 > 100
    r_422 = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "J1", "leak_area_cm2": 500.0, "noise_pct": 1.5},
        timeout=2.0,
    )
    assert r_422.status_code == 422, f"Expected 422, got {r_422.status_code}"

    # 422 on negative noise
    r_422_neg = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "J1", "leak_area_cm2": 25.0, "noise_pct": -5.0},
        timeout=2.0,
    )
    assert r_422_neg.status_code == 422

    # NumPy rejects negative RNG seeds; the API must validate them as 422, not leak a 500.
    r_422_seed = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "J1", "seed": -1},
        timeout=2.0,
    )
    assert r_422_seed.status_code == 422


def test_v1_1_revision_metadata():
    """Verify v1.1 network_revision and revision_meta contracts on /api/health and /api/network."""
    # 1. Health endpoint revision invariants
    r_health = requests.get(f"{BASE_URL}/api/health", timeout=2.0)
    assert r_health.status_code == 200
    h_model = HealthResponse.model_validate(r_health.json())
    assert h_model.network_revision is not None
    assert re.match(r"^rev-\d+-[0-9a-f]{8}$", h_model.network_revision)
    assert h_model.source in ["synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback"]
    assert isinstance(h_model.can_rollback, bool)

    # 2. Network endpoint revision metadata
    r_net = requests.get(f"{BASE_URL}/api/network", timeout=2.0)
    assert r_net.status_code == 200
    net_model = NetworkResponse.model_validate(r_net.json())
    assert net_model.network_revision == h_model.network_revision
    meta = net_model.revision_meta
    assert meta is not None
    assert meta.network_revision == h_model.network_revision
    assert meta.crs == "EPSG:4326"
    assert meta.num_nodes == len(net_model.nodes)
    assert meta.num_pipes == len(net_model.pipes)
    assert isinstance(meta.validation_summary, dict)


def test_v1_1_what_if_and_immutability():
    """Verify POST /api/what-if contract, validation gates, and active network immutability."""
    # 1. Capture baseline state
    r_net_before = requests.get(f"{BASE_URL}/api/network", timeout=2.0)
    assert r_net_before.status_code == 200
    net_before = r_net_before.json()
    rev_before = net_before["network_revision"]
    pipe0 = net_before["pipes"][0]

    r_sim_before = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "J15", "leak_area_cm2": 25.0, "noise_pct": 1.5, "seed": 42},
        timeout=5.0,
    )
    assert r_sim_before.status_code == 200
    sim_before = r_sim_before.json()

    # 2. Empty / no-op what-if scenario
    r_noop = requests.post(f"{BASE_URL}/api/what-if", json={}, timeout=5.0)
    assert r_noop.status_code == 200
    noop_model = WhatIfResponse.model_validate(r_noop.json())
    assert noop_model.converged is True
    max_delta = max(abs(v) for v in noop_model.node_pressure_deltas_m.values())
    assert max_delta < 1e-3, f"Expected no-op delta < 1e-3, got {max_delta}"

    # 3. Active what-if with pipe override, demand multiplier, reservoir head, and leaks
    r_active = requests.post(
        f"{BASE_URL}/api/what-if",
        json={
            "pipe_overrides": [{"pipe_id": pipe0["id"], "status": "CLOSED", "diameter_mm": 200.0, "roughness": 110.0}],
            "demand_multiplier": 1.2,
            "reservoir_head_delta_m": -5.0,
            "leaks": [{"node_id": "J15", "leak_area_cm2": 25.0}],
            "seed": 42,
        },
        timeout=5.0,
    )
    assert r_active.status_code == 200
    active_model = WhatIfResponse.model_validate(r_active.json())
    assert active_model.converged is True
    assert active_model.scenario.total_leak_loss_lpm > 0.0
    assert len(active_model.pipe_warnings) > 0
    assert len(active_model.limitations) >= 3

    # 4. Immutability: active network and subsequent leak simulations remain identical
    r_net_after = requests.get(f"{BASE_URL}/api/network", timeout=2.0)
    assert r_net_after.status_code == 200
    net_after = r_net_after.json()
    assert net_after["network_revision"] == rev_before
    pipe0_after = [p for p in net_after["pipes"] if p["id"] == pipe0["id"]][0]
    assert pipe0_after["status"] == "OPEN"

    r_sim_after = requests.post(
        f"{BASE_URL}/api/simulate-leak",
        json={"target_node_id": "J15", "leak_area_cm2": 25.0, "noise_pct": 1.5, "seed": 42},
        timeout=5.0,
    )
    assert r_sim_after.status_code == 200
    sim_after = r_sim_after.json()
    for field in ("actual_loss_lpm", "predicted_top_candidates", "sensor_readings", "node_heatmap"):
        assert sim_before[field] == sim_after[field]

    # 5. Validation gates
    assert requests.post(f"{BASE_URL}/api/what-if", json={"pipe_overrides": [{"pipe_id": "P_NONEXISTENT"}]}, timeout=2.0).status_code == 404
    assert requests.post(f"{BASE_URL}/api/what-if", json={"leaks": [{"node_id": "J_NONEXISTENT", "leak_area_cm2": 25.0}]}, timeout=2.0).status_code == 404
    assert requests.post(f"{BASE_URL}/api/what-if", json={"leaks": [{"node_id": "J1", "leak_area_cm2": 25.0}] * 6}, timeout=2.0).status_code == 422
    assert requests.post(f"{BASE_URL}/api/what-if", json={"demand_multiplier": 0.1}, timeout=2.0).status_code == 422
    assert requests.post(f"{BASE_URL}/api/what-if", json={"expected_network_revision": "rev-999-stale"}, timeout=2.0).status_code == 409


def test_v1_1_network_presets_and_intellisense():
    """Verify GET /api/network/presets and POST /api/network/intellisense contracts and validation gates."""
    # 1. Presets catalog
    r_presets = requests.get(f"{BASE_URL}/api/network/presets", timeout=2.0)
    assert r_presets.status_code == 200
    presets_model = NetworkPresetsListResponse.model_validate(r_presets.json())
    preset_map = {p.preset_id: p for p in presets_model.presets}
    assert "kengeri_baseline" in preset_map and preset_map["kengeri_baseline"].available is True
    assert "jayanagar_dma" in preset_map and preset_map["jayanagar_dma"].available is False
    assert "indiranagar_dma" in preset_map and preset_map["indiranagar_dma"].available is False

    # 2. IntelliSense
    r_net = requests.get(f"{BASE_URL}/api/network", timeout=2.0)
    assert r_net.status_code == 200
    net = r_net.json()
    rev = net["network_revision"]
    j0 = [n for n in net["nodes"] if n["type"] == "junction"][0]["id"]
    j1 = [n for n in net["nodes"] if n["type"] == "junction"][1]["id"]

    r_intel = requests.post(
        f"{BASE_URL}/api/network/intellisense",
        json={"from_node_id": j0, "to_node_id": j1, "expected_network_revision": rev},
        timeout=2.0,
    )
    assert r_intel.status_code == 200
    intel_model = IntelliSenseResponse.model_validate(r_intel.json())
    assert intel_model.length_m > 0.0
    assert intel_model.recommended_diameter_mm in [110, 150, 200, 250, 300, 400]
    assert intel_model.predicted_velocity_ms >= 0.0
    assert intel_model.predicted_headloss_m >= 0.0
    assert isinstance(intel_model.loop_candidates, list)

    # IntelliSense validation gates
    assert requests.post(f"{BASE_URL}/api/network/intellisense", json={"from_node_id": "J_NONEXISTENT"}, timeout=2.0).status_code == 404
    assert requests.post(f"{BASE_URL}/api/network/intellisense", json={"from_node_id": j0, "expected_network_revision": "rev-999-stale"}, timeout=2.0).status_code == 409


def test_v1_1_iot_and_pipe_condition_contracts():
    """Verify IoT state/telemetry ingest, pipe condition/RUL transparency, and auto-valve state machine."""
    # 1. IoT state query
    r_iot = requests.get(f"{BASE_URL}/api/iot/state?demo_tick=true&seed=42", timeout=2.0)
    assert r_iot.status_code == 200
    iot_model = IoTStateResponse.model_validate(r_iot.json())
    assert iot_model.auto_valve_enabled is False
    assert len(iot_model.latest_readings) > 0
    for pkt in iot_model.latest_readings.values():
        assert pkt.quality == "SIMULATED"
        assert pkt.source_type == "simulator"

    # 2. Telemetry Ingest with deduplication and validation
    unique_src = f"sensor_contract_{int(time.time() * 1000)}"
    valid_pkt = {
        "source_id": unique_src,
        "source_type": "simulator",
        "network_revision": iot_model.network_revision,
        "observed_at_utc": "2026-11-05T12:00:00Z",
        "target_id": "J72",
        "measurement_type": "pressure_m",
        "value": 37.0,
        "unit": "m",
        "sequence_id": 999,
        "quality": "SIMULATED",
    }
    dup_pkt = dict(valid_pkt)
    bad_tgt_pkt = dict(valid_pkt, source_id="bad_tgt", target_id="NONEXISTENT", sequence_id=1000)
    bad_unit_pkt = dict(valid_pkt, source_id="bad_unit", unit="m3h", sequence_id=1001)
    stale_rev_pkt = dict(valid_pkt, source_id="stale_rev", network_revision="rev-999-bad", sequence_id=1002)

    r_ingest = requests.post(
        f"{BASE_URL}/api/iot/ingest",
        json={"packets": [valid_pkt, dup_pkt, bad_tgt_pkt, bad_unit_pkt, stale_rev_pkt]},
        headers=ADMIN_HEADERS,
        timeout=2.0,
    )
    assert r_ingest.status_code == 200
    ingest_model = TelemetryIngestResponse.model_validate(r_ingest.json())
    assert ingest_model.accepted >= 1
    assert ingest_model.deduplicated >= 1
    assert ingest_model.rejected >= 1

    # 3. Pipe Condition / RUL transparency
    r_pipes = requests.get(f"{BASE_URL}/api/pipes/condition", timeout=2.0)
    assert r_pipes.status_code == 200
    pipes_model = PipeConditionResponse.model_validate(r_pipes.json())
    assert pipes_model.rul_available is False
    assert len(pipes_model.methodology_note) > 0
    assert len(pipes_model.pipes) > 0
    for p in pipes_model.pipes:
        assert p.rul_status == "unavailable"
        assert p.rul_years is None
        assert 0.0 <= p.illustrative_stress_index <= 1.0
        assert len(p.missing_inputs) > 0

    # 4. Safe Auto-Valve State Machine
    rev = iot_model.network_revision
    # Refused by default
    r_refused = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "confirm_close", "confirm": True}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_refused.status_code == 200
    assert r_refused.json()["status"] == "refused"

    # Enable mode
    r_enable = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "set_mode", "enable_auto_valve": True}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_enable.status_code == 200
    assert r_enable.json()["auto_valve_enabled"] is True

    # Propose
    r_prop = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "propose", "target_node_id": "J15", "leak_area_cm2": 25.0}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_prop.status_code == 200
    prop_data = r_prop.json()
    assert prop_data["status"] == "proposed"
    proposal_id = prop_data["proposal"]["proposal_id"]

    # Confirm close
    r_conf = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "confirm_close", "proposal_id": proposal_id, "confirm": True}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_conf.status_code == 200
    assert r_conf.json()["status"] == "closed_in_simulation"
    assert r_conf.json()["simulation_only"] is True

    # Emergency stop
    r_estop = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "emergency_stop"}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_estop.status_code == 200
    assert r_estop.json()["status"] == "stopped"
    assert r_estop.json()["emergency_stop"] is True

    # Reset valves
    r_reset = requests.post(f"{BASE_URL}/api/iot/auto-valve", json={"action": "reset_valves"}, headers=ADMIN_HEADERS, timeout=2.0)
    assert r_reset.status_code == 200
    assert r_reset.json()["status"] == "reset"
    assert r_reset.json()["active_valve_closures"] == []

