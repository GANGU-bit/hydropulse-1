Exit code: 0
Wall time: 2.7 seconds
Output:
"""
Build realistic demo presets from a committed EPANET network model (.inp).
Selects:
  1. Mainline: Junction touching the largest diameter pipe (not sensor, not reservoir-adjacent)
  2. Lane seep: Degree-1 dead-end junction farthest from reservoir
  3. Joint crack: Junction with median betweenness centrality (not sensor)

Outputs: data_utils/presets.json (validated via PresetsResponse).
"""

import json
import os
import sys
from pathlib import Path
import networkx as nx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import Preset, PresetsResponse


def build_presets(
    inp_path: str = "backend/bangalore_kengeri.inp",
    meta_path: str = "backend/network_meta.json",
    out_path: str = "data_utils/presets.json",
):
    inp_file = REPO_ROOT / inp_path
    meta_file = REPO_ROOT / meta_path
    out_file = REPO_ROOT / out_path

    sensor_ids = set()
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                sensor_ids = set(meta.get("sensor_ids", []))
        except Exception as e:
            print(f"Warning reading meta file: {e}")

    if not inp_file.exists():
        raise FileNotFoundError(
            f"Committed network is required to build real presets: {inp_file}. "
            "Do not copy synthetic mock IDs into data_utils/presets.json."
        )

    # Load with WNTR
    import wntr

    wn = wntr.network.WaterNetworkModel(str(inp_file))

    # Graph representation
    try:
        G = wn.to_graph().to_undirected()
    except Exception:
        G = wntr.network.to_graph(wn).to_undirected()

    junctions = set(wn.junction_name_list)
    sources = set(wn.reservoir_name_list + wn.tank_name_list)

    # Reservoir-adjacent nodes
    res_adjacent = set()
    for s in sources:
        if s in G:
            res_adjacent.update(G.neighbors(s))

    # Fallback sensors if not loaded from meta
    if not sensor_ids:
        # Pick top 5 separated nodes by betweenness centrality
        bc = nx.betweenness_centrality(G, weight="length")
        sorted_bc = sorted(junctions, key=lambda j: bc.get(j, 0), reverse=True)
        chosen = []
        for cand in sorted_bc:
            if all(nx.shortest_path_length(G, cand, other) >= 3 for other in chosen):
                chosen.append(cand)
            if len(chosen) == 5:
                break
        sensor_ids = set(chosen)

    # 1. Mainline: touching largest pipe diameter, not sensor, not res-adjacent
    max_dia = -1.0
    for p_name, pipe in wn.pipes():
        if pipe.diameter > max_dia:
            max_dia = pipe.diameter

    mainline_candidates = []
    for p_name, pipe in wn.pipes():
        if abs(pipe.diameter - max_dia) < 1e-4:
            for node_name in [pipe.start_node_name, pipe.end_node_name]:
                if (
                    node_name in junctions
                    and node_name not in sensor_ids
                    and node_name not in res_adjacent
                ):
                    mainline_candidates.append(node_name)

    if not mainline_candidates:
        mainline_candidates = [j for j in junctions if j not in sensor_ids and j not in res_adjacent]
    mainline_node = sorted(mainline_candidates)[0]

    # 2. Lane seep: degree-1 dead-end junction farthest from reservoir
    primary_source = list(sources)[0] if sources else None
    dead_ends = [j for j in junctions if G.degree(j) == 1 and j != primary_source]
    if not dead_ends:
        # Junctions with minimum degree
        min_deg = min(G.degree(j) for j in junctions)
        dead_ends = [j for j in junctions if G.degree(j) == min_deg]

    farthest_dead_end = None
    max_dist = -1
    for d in dead_ends:
        if primary_source and nx.has_path(G, primary_source, d):
            dist = nx.shortest_path_length(G, primary_source, d, weight="length")
            if dist > max_dist:
                max_dist = dist
                farthest_dead_end = d
        else:
            farthest_dead_end = d

    lane_seep_node = farthest_dead_end if farthest_dead_end else sorted(dead_ends)[-1]

    # 3. Joint crack: junction with median betweenness centrality, not sensor
    bc = nx.betweenness_centrality(G, weight="length")
    eligible_juncs = [
        j for j in junctions
        if j not in sensor_ids
        and j != mainline_node
        and j != lane_seep_node
    ]
    if not eligible_juncs:
        eligible_juncs = list(junctions)

    sorted_by_bc = sorted(eligible_juncs, key=lambda j: bc.get(j, 0.0))
    median_idx = len(sorted_by_bc) // 2
    joint_crack_node = sorted_by_bc[median_idx]

    presets = [
        Preset(
            id="kengeri_mainline_rupture",
            label="Kengeri Mainline Rupture",
            description=f"45 cm2 rupture on main trunk corridor at {mainline_node}",
            target_node_id=mainline_node,
            leak_area_cm2=45.0,
            noise_pct=1.5,
        ),
        Preset(
            id="residential_lane_seep",
            label="Residential Lane Seep",
            description=f"8 cm2 seep in residential dead-end lane at {lane_seep_node}",
            target_node_id=lane_seep_node,
            leak_area_cm2=8.0,
            noise_pct=3.0,
        ),
        Preset(
            id="joint_crack_junction",
            label="Joint Crack under Junction",
            description=f"20 cm2 cracked joint at secondary intersection at {joint_crack_node}",
            target_node_id=joint_crack_node,
            leak_area_cm2=20.0,
            noise_pct=2.0,
        ),
    ]

    resp = PresetsResponse(presets=presets)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(resp.model_dump(), f, indent=2)

    print(f"Successfully generated presets in {out_file}:")
    for p in presets:
        print(f"  - {p.id}: node {p.target_node_id}, {p.leak_area_cm2} cm2, {p.noise_pct}% noise")


if __name__ == "__main__":
    build_presets()

