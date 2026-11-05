"""Backend test suite (01_hardhik_plan.md PART 5). Run: pytest -q backend/tests"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.hydraulic_engine import HydraulicEngine, cm2_to_m2, tankers_per_day
from data_utils.schemas import LeakSimulationResponse, NetworkResponse


@pytest.fixture(scope="session")
def engine() -> HydraulicEngine:
    return HydraulicEngine().boot()


@pytest.fixture(scope="session")
def client():
    from backend.main import app

    with TestClient(app) as c:
        yield c


# ── units ──
def test_units_cm2_to_m2_and_plausible_loss(engine):
    assert cm2_to_m2(25.0) == pytest.approx(0.0025)
    j = engine.junctions[len(engine.junctions) // 2]
    _, leak_m3s = engine._solve_with_leak(j, 1.0)
    lpm = leak_m3s * 60000
    # Torricelli: Q = Cd·A·√(2gh); 1 cm² at ~40–60 m ≈ 130–160 LPM. Wrong units would be off by 10⁴.
    assert 50 < lpm < 2000, lpm
    assert tankers_per_day(482.1) == 115.7


# ── sensors ──
def test_sensors_five_distinct_separated_junctions(engine):
    s = engine.sensor_ids
    assert len(s) == 5 and len(set(s)) == 5
    assert all(x in engine.junctions for x in s)
    for i, a in enumerate(s):
        hops = nx.single_source_shortest_path_length(engine.graph, a)
        for b in s[i + 1:]:
            assert hops[b] >= engine.min_hop_separation


# ── localisation ──
def test_self_localization_noise_free(engine):
    hits = sum(
        engine.simulate_and_triangulate(j, 25.0, 0.0)["predicted_top_candidates"][0]["node_id"] == j
        for j in engine.junctions
    )
    assert hits / len(engine.junctions) >= 0.90


def test_results_do_not_drift_between_requests(engine):
    j = engine.junctions[3]
    a = engine.simulate_and_triangulate(j, 30.0, 1.5, seed=11)
    engine.simulate_and_triangulate(engine.junctions[-1], 80.0, 5.0, seed=1)
    b = engine.simulate_and_triangulate(j, 30.0, 1.5, seed=11)
    for k in ("actual_loss_lpm", "predicted_top_candidates", "sensor_readings", "node_heatmap"):
        assert a[k] == b[k]


def test_fast_path_matches_wntr_simulator(engine):
    if engine._fast is None:
        pytest.skip("network uses the WNTRSimulator path directly")
    for j, area in [(engine.junctions[0], 0.5), (engine.junctions[10], 25.0), (engine.junctions[-1], 90.0)]:
        p_fast, q_fast = engine._solve_with_leak(j, area)
        p_ref, q_ref = engine._solve_with_leak_wntr(j, area)
        assert np.max(np.abs(p_fast - p_ref)) < 1e-4
        assert abs(q_fast - q_ref) < 1e-6


def test_reservoir_rejected(engine):
    with pytest.raises(KeyError):
        engine.simulate_and_triangulate(engine.sources[0], 25.0, 1.5)


# ── isolation ──
def test_isolation_cuts_leak_node_from_all_sources(engine):
    for j in engine.junctions[::7]:
        plan = engine.isolation_plan(j)
        g = engine.graph.copy()
        g.remove_edges_from([(u, v) for u, v, d in engine.graph.edges(data=True)
                             if d["link"] in set(plan["recommended_valves_to_close"])])
        assert all(not nx.has_path(g, j, s) for s in engine.sources)
        assert j in plan["isolated_node_ids"]
        assert plan["affected_households_after_isolation"] >= engine.households[j]


def test_simulate_isolation_reports_outage(engine):
    j = engine.junctions[5]
    plan = engine.isolation_plan(j)
    out = engine.simulate_isolation(plan["recommended_valves_to_close"], j, 25.0)
    assert j in out["nodes_without_supply"]
    assert out["min_pressure_m"] > 0
    assert set(out["node_pressures"]) == set(engine.junctions)


# ── API ──
def test_api_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["matrix_ready"] is True and len(body["sensor_ids"]) == 5


def test_api_network_schema(client):
    r = client.get("/api/network")
    assert r.status_code == 200
    net = NetworkResponse.model_validate(r.json())
    assert sum(n.is_sensor for n in net.nodes) == 5
    assert {p.road_class for p in net.pipes} <= {"primary", "secondary", "residential", "feeder"}
    for n in net.nodes:
        assert 12.70 < n.lat < 13.30 and 77.30 < n.lng < 77.90


def test_api_presets_reference_real_junctions(client):
    junctions = {n["id"] for n in client.get("/api/network").json()["nodes"] if n["type"] == "junction"}
    presets = client.get("/api/presets").json()["presets"]
    assert len(presets) == 3
    assert all(p["target_node_id"] in junctions for p in presets)


def test_api_simulate_leak_happy_path(client):
    health = client.get("/api/health").json()
    preset = client.get("/api/presets").json()["presets"][0]
    r = client.post("/api/simulate-leak", json={
        "target_node_id": preset["target_node_id"], "leak_area_cm2": preset["leak_area_cm2"],
        "noise_pct": preset["noise_pct"], "seed": 7})
    assert r.status_code == 200
    body = LeakSimulationResponse.model_validate(r.json())
    assert [s.sensor_id for s in body.sensor_readings] == health["sensor_ids"]
    assert len(body.node_heatmap) == health["num_junctions"]
    assert max(body.node_heatmap.values()) == 1.0
    assert all(0.0 <= v <= 1.0 for v in body.node_heatmap.values())
    c = body.predicted_top_candidates[0]
    assert c.tankers_lost_per_day == pytest.approx(round(c.estimated_loss_lpm * 0.24, 1), abs=0.11)
    assert body.recommended_valves_to_close and body.isolated_node_ids


def test_api_unknown_junction_404(client):
    r = client.post("/api/simulate-leak", json={"target_node_id": "NOPE"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Unknown junction id: NOPE"}
    r = client.post("/api/simulate-leak", json={"target_node_id": "R1"})
    assert r.status_code == 404


@pytest.mark.parametrize("area", [0, 500])
def test_api_validation_422(client, area):
    j = client.get("/api/health").json()["sensor_ids"][0]
    r = client.post("/api/simulate-leak", json={"target_node_id": j, "leak_area_cm2": area})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_api_negative_seed_is_422_not_500(client):
    j = client.get("/api/health").json()["sensor_ids"][0]
    r = client.post("/api/simulate-leak", json={"target_node_id": j, "seed": -1})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_api_internal_keyerror_is_500_not_404(client, monkeypatch):
    from backend import main

    def boom(*_a, **_k):
        raise KeyError("internal")

    monkeypatch.setattr(main.engine, "simulate_and_triangulate", boom)
    j = client.get("/api/health").json()["sensor_ids"][0]
    r = client.post("/api/simulate-leak", json={"target_node_id": j})
    assert r.status_code == 500
    assert r.json()["detail"].startswith("Simulation failed:")


def test_unconverged_solve_raises(engine, monkeypatch):
    class Fake:
        error_code = 1

    monkeypatch.setattr("wntr.sim.WNTRSimulator.run_sim", lambda self, **kw: Fake())
    with pytest.raises(RuntimeError):
        engine._solve()


def test_api_isolate_unknown_pipe_404(client):
    j = client.get("/api/health").json()["sensor_ids"][0]
    r = client.post("/api/isolate", json={"valve_ids": ["NOPE"], "target_node_id": j, "leak_area_cm2": 25})
    assert r.status_code == 404


# ── Net3 fallback ──
def test_net3_fallback_when_inp_missing(tmp_path, caplog):
    eng = HydraulicEngine(inp_path=str(tmp_path / "missing.inp"), cache_path=str(tmp_path / "m.npy"))
    assert eng.is_fallback and eng.network_name == "net3_fallback"
    assert "FALLING BACK" in caplog.text
    eng.run_baseline()
    assert len(eng.select_sentinel_sensors()) == 5
    payload = eng.get_network_payload()
    assert all(isinstance(n["id"], str) for n in payload["nodes"])
