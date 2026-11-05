"""
Generate realistic mock fixtures for HydroPulse frontend.
Writes to frontend/public/mock/:
  - network.json
  - presets.json
  - simulate_leak_<preset_id>.json (for each preset)
  - simulate_leak_default.json
  - health.json
  - README.md

All JSON outputs are validated against data_utils/schemas.py.
"""

import hashlib
import json
import math
import os
import sys
from pathlib import Path

# Ensure root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import (
    NetworkResponse,
    NetworkNode,
    NetworkPipe,
    Center,
    HealthResponse,
    Preset,
    PresetsResponse,
    LeakSimulationResponse,
    CandidateNode,
    SensorReading,
    lpm_to_tankers_per_day,
)
from data_utils.geo_mapper import KENGERI_BBOX, haversine_m


def build_mock_fixtures():
    mock_dir = REPO_ROOT / "frontend" / "public" / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)

    rows, cols = 8, 10
    total_junctions = rows * cols  # 80

    lat_min = KENGERI_BBOX["lat_min"]
    lat_max = KENGERI_BBOX["lat_max"]
    lng_min = KENGERI_BBOX["lng_min"]
    lng_max = KENGERI_BBOX["lng_max"]

    # Pick 5 spread-out sentinel sensor junctions
    # Rows: 1, 3, 4, 6, 7; Cols: 1, 7, 4, 2, 8
    sensor_nodes = ["J12", "J38", "J45", "J63", "J79"]

    nodes_dict = {}
    nodes_list = []

    # Create Junctions J1..J80
    for r in range(rows):
        for c in range(cols):
            node_id = f"J{r * cols + c + 1}"
            lat = round(lat_min + (r / (rows - 1)) * (lat_max - lat_min), 6)
            lng = round(lng_min + (c / (cols - 1)) * (lng_max - lng_min), 6)
            elevation = round(812.0 + (r * 1.2) + (c * 0.8), 2)
            base_p = round(45.0 - (elevation - 812.0) * 0.8, 2)
            base_p = max(22.0, min(54.0, base_p))
            demand = round(0.15 + ((r * 3 + c * 7) % 11) * 0.05, 2)
            households = int(25 + ((r * 7 + c * 13) % 45))
            is_sens = node_id in sensor_nodes

            node_obj = NetworkNode(
                id=node_id,
                type="junction",
                lat=lat,
                lng=lng,
                elevation_m=elevation,
                base_pressure_m=base_p,
                base_demand_lps=demand,
                households=households,
                is_sensor=is_sens,
                leak_probability=0.0,
            )
            nodes_dict[node_id] = node_obj
            nodes_list.append(node_obj)

    # Reservoir R1
    res_lat = round(lat_max + 0.003, 6)
    res_lng = round((lng_min + lng_max) / 2.0, 6)
    res_node = NetworkNode(
        id="R1",
        type="reservoir",
        lat=res_lat,
        lng=res_lng,
        elevation_m=865.0,
        base_pressure_m=0.0,
        base_demand_lps=0.0,
        households=0,
        is_sensor=False,
        leak_probability=0.0,
    )
    nodes_dict["R1"] = res_node
    nodes_list.append(res_node)

    # Generate Pipes
    pipes_list = []
    pipe_counter = 1

    # Feeder pipe P0 from R1 to J75 (top middle junction)
    feeder_target = f"J{7 * cols + 5}"
    feeder_pipe = NetworkPipe(
        id="P0",
        source="R1",
        target=feeder_target,
        diameter_mm=500.0,
        length_m=65.0,
        flow_m3h=285.0,
        status="OPEN",
        road_class="feeder",
    )
    pipes_list.append(feeder_pipe)

    # Horizontal pipes
    for r in range(rows):
        for c in range(cols - 1):
            src = f"J{r * cols + c + 1}"
            tgt = f"J{r * cols + c + 2}"
            dist = haversine_m(
                nodes_dict[src].lat, nodes_dict[src].lng,
                nodes_dict[tgt].lat, nodes_dict[tgt].lng
            )
            is_primary = (r == 3 or r == 4)
            is_secondary = (r == 1 or r == 6)
            dia = 400.0 if is_primary else (250.0 if is_secondary else 110.0)
            road_cls = "primary" if is_primary else ("secondary" if is_secondary else "residential")
            flow = round(15.0 + ((r * 5 + c * 3) % 25) * 4.2, 1)

            pipe_obj = NetworkPipe(
                id=f"P{pipe_counter}",
                source=src,
                target=tgt,
                diameter_mm=dia,
                length_m=dist,
                flow_m3h=flow,
                status="OPEN",
                road_class=road_cls,
            )
            pipes_list.append(pipe_obj)
            pipe_counter += 1

    # Vertical pipes
    for r in range(rows - 1):
        for c in range(cols):
            src = f"J{r * cols + c + 1}"
            tgt = f"J{(r + 1) * cols + c + 1}"
            dist = haversine_m(
                nodes_dict[src].lat, nodes_dict[src].lng,
                nodes_dict[tgt].lat, nodes_dict[tgt].lng
            )
            is_primary = (c == 4 or c == 5)
            is_secondary = (c == 2 or c == 7)
            dia = 400.0 if is_primary else (250.0 if is_secondary else 110.0)
            road_cls = "primary" if is_primary else ("secondary" if is_secondary else "residential")
            flow = round(20.0 + ((r * 7 + c * 4) % 20) * 3.8, 1)

            pipe_obj = NetworkPipe(
                id=f"P{pipe_counter}",
                source=src,
                target=tgt,
                diameter_mm=dia,
                length_m=dist,
                flow_m3h=flow,
                status="OPEN",
                road_class=road_cls,
            )
            pipes_list.append(pipe_obj)
            pipe_counter += 1

    # Build NetworkResponse
    center_lat = round((lat_min + lat_max) / 2.0, 6)
    center_lng = round((lng_min + lng_max) / 2.0, 6)
    network_resp = NetworkResponse(
        network_name="bangalore_kengeri_mock",
        center=Center(lat=center_lat, lng=center_lng),
        sensor_ids=sensor_nodes,
        nodes=nodes_list,
        pipes=pipes_list,
    )

    with open(mock_dir / "network.json", "w", encoding="utf-8") as f:
        json.dump(network_resp.model_dump(), f, indent=2)

    # HealthResponse
    health_resp = HealthResponse(
        status="ok",
        network_name="bangalore_kengeri_mock",
        num_junctions=total_junctions,
        num_pipes=len(pipes_list),
        sensor_ids=sensor_nodes,
        matrix_ready=True,
    )
    with open(mock_dir / "health.json", "w", encoding="utf-8") as f:
        json.dump(health_resp.model_dump(), f, indent=2)

    # Presets
    presets_list = [
        Preset(
            id="kengeri_mainline_rupture",
            label="Kengeri Mainline Rupture",
            description="45 cm2 rupture on primary distribution corridor",
            target_node_id="J35",  # J35 touches primary 400mm pipe
            leak_area_cm2=45.0,
            noise_pct=1.5,
        ),
        Preset(
            id="residential_lane_seep",
            label="Residential Lane Seep",
            description="8 cm2 seep in residential perimeter dead-end lane",
            target_node_id="J80",  # Corner perimeter node
            leak_area_cm2=8.0,
            noise_pct=3.0,
        ),
        Preset(
            id="joint_crack_junction",
            label="Joint Crack under Junction",
            description="20 cm2 cracked joint at secondary intersection",
            target_node_id="J44",  # Secondary crossroads
            leak_area_cm2=20.0,
            noise_pct=2.0,
        ),
    ]
    presets_resp = PresetsResponse(presets=presets_list)
    with open(mock_dir / "presets.json", "w", encoding="utf-8") as f:
        json.dump(presets_resp.model_dump(), f, indent=2)

    # Incident pipes map (for valve isolation)
    incident_pipes = {}
    for p in pipes_list:
        incident_pipes.setdefault(p.source, []).append(p.id)
        incident_pipes.setdefault(p.target, []).append(p.id)

    # Simulate Leak files for each preset and default
    def make_leak_response(target_id: str, area_cm2: float, noise_pct: float) -> LeakSimulationResponse:
        target_node = nodes_dict[target_id]
        # Orifice flow: Q = Cd * A * sqrt(2*g*h)
        cd = 0.75
        area_m2 = area_cm2 / 10000.0
        h_eff = max(10.0, target_node.base_pressure_m * 0.85)
        loss_m3s = cd * area_m2 * math.sqrt(2 * 9.81 * h_eff)
        loss_lpm = round(loss_m3s * 60000.0, 1)

        # Distances from all junctions to target
        dist_map = {}
        for j_id, j_node in nodes_dict.items():
            if j_node.type == "junction":
                dist_map[j_id] = haversine_m(target_node.lat, target_node.lng, j_node.lat, j_node.lng)

        # Heatmap: decay with distance, peak = 1.0 at target
        node_heatmap = {}
        for j_id, dist in dist_map.items():
            val = math.exp(-dist / 140.0) ** 1.6
            node_heatmap[j_id] = round(max(0.01, min(1.0, val)), 3)
        node_heatmap[target_id] = 1.0

        # Sensor readings
        readings = []
        for s_id in sensor_nodes:
            s_node = nodes_dict[s_id]
            dist_s = dist_map[s_id]
            # Pressure drop decays with distance
            drop = round((5.8 * (area_cm2 / 25.0)) / (1.0 + (dist_s / 200.0) ** 1.2), 2)
            digest = hashlib.sha256(f"{s_id}:{target_id}".encode("utf-8")).digest()
            noise = round(((digest[0] % 19) - 9) * 0.01 * noise_pct, 2)
            drop_m = max(0.1, round(drop + noise, 2))
            baseline_m = s_node.base_pressure_m
            observed_m = round(baseline_m - drop_m, 2)
            readings.append(
                SensorReading(
                    sensor_id=s_id,
                    baseline_m=baseline_m,
                    observed_m=observed_m,
                    drop_m=drop_m,
                )
            )

        # Top 5 candidate nodes (target + 4 nearest)
        sorted_by_dist = sorted(dist_map.keys(), key=lambda k: dist_map[k])
        top_5_ids = sorted_by_dist[:5]

        candidates = []
        conf_scales = [95.2 - noise_pct * 1.5, 74.0, 56.5, 41.0, 26.5]
        for idx, c_id in enumerate(top_5_ids):
            conf = round(conf_scales[idx], 1)
            est_loss = round(loss_lpm * (0.98 if idx == 0 else (0.85 - idx * 0.1)), 1)
            tankers = lpm_to_tankers_per_day(est_loss)
            est_area = round(area_cm2 * (0.98 if idx == 0 else (0.85 - idx * 0.1)), 1)
            candidates.append(
                CandidateNode(
                    node_id=c_id,
                    confidence_pct=conf,
                    estimated_loss_lpm=est_loss,
                    tankers_lost_per_day=tankers,
                    estimated_leak_area_cm2=est_area,
                )
            )

        valves = sorted(incident_pipes.get(target_id, []))
        isolated = [target_id]
        affected_hh = target_node.households

        return LeakSimulationResponse(
            injected_node_id=target_id,
            injected_leak_area_cm2=area_cm2,
            actual_loss_lpm=loss_lpm,
            predicted_top_candidates=candidates,
            sensor_readings=readings,
            node_heatmap=node_heatmap,
            recommended_valves_to_close=valves,
            isolated_node_ids=isolated,
            affected_households_after_isolation=affected_hh,
            localization_error_m=0.0,
            compute_time_ms=284.5,
        )

    for p in presets_list:
        sim_resp = make_leak_response(p.target_node_id, p.leak_area_cm2, p.noise_pct)
        file_path = mock_dir / f"simulate_leak_{p.id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(sim_resp.model_dump(), f, indent=2)

    # Default leak response (on J15, 25 cm2)
    default_resp = make_leak_response("J15", 25.0, 1.5)
    with open(mock_dir / "simulate_leak_default.json", "w", encoding="utf-8") as f:
        json.dump(default_resp.model_dump(), f, indent=2)

    # Mock README
    readme_content = """# HydroPulse Mock Fixtures

Phase A mock data for frontend development and offline fallback.
All fixtures strictly mirror API Contract v1.0 and validate against `data_utils.schemas`.

## Endpoint to Fixture Mapping

| Endpoint | Fixture File |
| :--- | :--- |
| `GET /api/network` | `network.json` |
| `GET /api/presets` | `presets.json` |
| `GET /api/health` | `health.json` |
| `POST /api/simulate-leak` | `simulate_leak_<preset_id>.json` |
| `POST /api/simulate-leak` (fallback) | `simulate_leak_default.json` |

## Presets Included
- `kengeri_mainline_rupture` (target J35, 45 cmÂ², 1.5% noise)
- `residential_lane_seep` (target J80, 8 cmÂ², 3% noise)
- `joint_crack_junction` (target J44, 20 cmÂ², 2% noise)
"""
    with open(mock_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    print("Successfully built and validated all mock fixtures in frontend/public/mock/!")


if __name__ == "__main__":
    build_mock_fixtures()

