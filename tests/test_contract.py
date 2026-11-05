Exit code: 0
Wall time: 4.5 seconds
Output:
"""
API Contract tests for HydroPulse live backend.
Validates live HTTP responses against Pydantic models from data_utils.schemas.
Skips automatically if the backend is not reachable.
"""

import os
import sys
import time
from pathlib import Path
import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import (
    HealthResponse,
    NetworkResponse,
    PresetsResponse,
    LeakSimulationResponse,
    lpm_to_tankers_per_day,
)

BASE_URL = os.environ.get("BASE", "http://localhost:8000").rstrip("/")


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

