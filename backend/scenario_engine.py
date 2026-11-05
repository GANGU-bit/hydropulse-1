"""
HydroPulse Scenario Engine (v1.1).

Counterfactual what-if simulation, hydraulic capacity stress-testing,
valve manipulation, pipe sizing/roughness overrides, and leak injection
using steady-state PDD modeling on cloned WNTR networks.
"""

from __future__ import annotations

import copy
import math
import random
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException
import networkx as nx
import numpy as np
import wntr
import wntr.network
import wntr.sim

from data_utils.schemas import (
    MINIMUM_PRESSURE_M,
    PRESSURE_EXPONENT,
    REQUIRED_PRESSURE_M,
    TANKER_LITRES,
    PipeWarning,
    WhatIfLeakInjection,
    WhatIfMetrics,
    WhatIfPipeOverride,
    WhatIfRequest,
    WhatIfResponse,
    lpm_to_tankers_per_day,
)

# Discharge coefficient for orifices / leaks (SOURCE_OF_TRUTH §6)
DISCHARGE_COEFF: float = 0.75

# Standard limitations disclaimer
LIMITATIONS: List[str] = [
    "Steady-state PDD snapshot (duration=0); transient surge and water hammer not modeled.",
    "Counterfactual scenario only; not an operational command or automated valve actuation directive.",
    "Pipe headloss computed using Hazen-Williams formulation on nominal diameters.",
    "Demands are modeled as pressure-dependent (PDD) with minimum pressure 2.0m and required pressure 15.0m.",
]


def _natural_key(s: Any) -> tuple[Any, ...]:
    """Natural sort key for alphanumeric IDs (e.g. 'J1' < 'J2' < 'J10')."""
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(s)))


def get_households(engine: Any, node_id: str) -> int:
    """Retrieve connected household count for a junction with fallbacks."""
    if hasattr(engine, "households_for") and callable(engine.households_for):
        try:
            return int(engine.households_for(node_id))
        except Exception:
            pass
    if hasattr(engine, "households") and isinstance(engine.households, dict):
        return int(engine.households.get(node_id, 0))
    if hasattr(engine, "meta") and isinstance(engine.meta, dict):
        nodes = engine.meta.get("nodes", {})
        if node_id in nodes:
            return int(nodes[node_id].get("households", 0))
        hh_dict = engine.meta.get("households", {})
        if node_id in hh_dict:
            return int(hh_dict[node_id])
    return 0


def get_road_class(engine: Any, pid: str, wn: Any = None) -> str:
    """Determine pipe road classification (primary, secondary, residential, feeder)."""
    if hasattr(engine, "road_class") and isinstance(engine.road_class, dict):
        rc = engine.road_class.get(pid)
        if rc:
            return str(rc)
    if hasattr(engine, "meta") and isinstance(engine.meta, dict):
        pipes = engine.meta.get("pipes", {})
        if pid in pipes and "road_class" in pipes[pid]:
            return str(pipes[pid]["road_class"])
        rc_dict = engine.meta.get("road_class", {})
        if pid in rc_dict:
            return str(rc_dict[pid])
    if hasattr(engine, "_derive_road_class") and callable(engine._derive_road_class):
        try:
            return str(engine._derive_road_class(pid))
        except Exception:
            pass
    if wn is not None:
        try:
            link = wn.get_link(pid)
            d = float(getattr(link, "diameter", 0.0) or 0.0)
            if d >= 0.40 - 1e-9:
                return "primary"
            if d >= 0.20:
                return "secondary"
            return "residential"
        except Exception:
            pass
    return "residential"


def get_open_links_graph(wn: wntr.network.WaterNetworkModel) -> nx.Graph:
    """Construct an undirected graph of all currently open links."""
    g = nx.Graph()
    g.add_nodes_from(wn.node_name_list)
    for name in wn.link_name_list:
        link = wn.get_link(name)
        status = getattr(link, "initial_status", None)
        if status == wntr.network.LinkStatus.Closed:
            continue
        w = float(getattr(link, "length", 1.0) or 1.0)
        g.add_edge(link.start_node_name, link.end_node_name, link=name, weight=w)
    return g


def find_cut_off_junctions(
    wn: wntr.network.WaterNetworkModel,
    g: nx.Graph,
    junctions: list[str],
) -> set[str]:
    """Identify junctions disconnected from all reservoir and tank sources."""
    sources = list(wn.reservoir_name_list) + list(wn.tank_name_list)
    reachable: set[str] = set()
    for s in sources:
        if s in g:
            reachable |= nx.node_connected_component(g, s)
    return set(junctions) - reachable


def compute_baseline_metrics(engine: Any) -> WhatIfMetrics:
    """Compute baseline WhatIfMetrics directly from engine state without mutating."""
    junctions = list(getattr(engine, "junctions", []))
    if not junctions and hasattr(engine, "wn"):
        junctions = list(engine.wn.junction_name_list)

    baseline_p = getattr(engine, "baseline_pressure", {}) or {}
    if not baseline_p and hasattr(engine, "run_baseline") and callable(engine.run_baseline):
        engine.run_baseline()
        baseline_p = getattr(engine, "baseline_pressure", {}) or {}

    # Disconnectivity in baseline
    if hasattr(engine, "wn"):
        g = get_open_links_graph(engine.wn)
        cut_off = find_cut_off_junctions(engine.wn, g, junctions)
    else:
        cut_off = set()

    pressures = [float(baseline_p.get(j, 0.0)) for j in junctions]

    min_p = round(min(pressures), 2) if pressures else 0.0
    avg_p = round(sum(pressures) / len(pressures), 2) if pressures else 0.0
    max_p = round(max(pressures), 2) if pressures else 0.0
    deficit_count = sum(1 for p in pressures if p < REQUIRED_PRESSURE_M)

    unsupplied = sorted(
        [j for j in junctions if j in cut_off or float(baseline_p.get(j, 0.0)) <= MINIMUM_PRESSURE_M],
        key=_natural_key,
    )
    affected_hh = sum(get_households(engine, j) for j in unsupplied)

    return WhatIfMetrics(
        min_pressure_m=min_p,
        avg_pressure_m=avg_p,
        max_pressure_m=max_p,
        pressure_deficit_nodes=deficit_count,
        unsupplied_nodes=unsupplied,
        affected_households=affected_hh,
        total_leak_loss_lpm=0.0,
        tankers_lost_per_day=0.0,
    )


def compute_pipe_warnings(
    engine: Any,
    wn: wntr.network.WaterNetworkModel,
    sim_res: Any,
) -> list[PipeWarning]:
    """Compute hydraulic velocity, headloss, and closed valve warnings."""
    warnings: list[PipeWarning] = []
    link_names = sorted(
        wn.pipe_name_list if hasattr(wn, "pipe_name_list") else wn.link_name_list,
        key=_natural_key,
    )

    for pid in link_names:
        pipe = wn.get_link(pid)

        # Check if closed
        is_closed = (
            pipe.initial_status == wntr.network.LinkStatus.Closed
            or getattr(pipe, "status", None) == wntr.network.LinkStatus.Closed
            or (
                hasattr(sim_res, "link")
                and "status" in sim_res.link
                and pid in sim_res.link["status"].columns
                and float(sim_res.link["status"].loc[0, pid]) == 0.0
            )
        )

        if is_closed:
            warnings.append(
                PipeWarning(
                    pipe_id=pid,
                    warning_type="closed",
                    value=0.0,
                    unit="m/s",
                    message=f"Pipe {pid} is closed",
                )
            )
            continue

        # Open pipe metrics
        flow_m3s = (
            float(sim_res.link["flowrate"].loc[0, pid])
            if hasattr(sim_res, "link") and "flowrate" in sim_res.link and pid in sim_res.link["flowrate"].columns
            else 0.0
        )
        d_m = float(getattr(pipe, "diameter", 0.0) or 0.0)
        area = math.pi * (d_m / 2.0) ** 2 if d_m > 0 else 0.0
        v = abs(flow_m3s) / area if area > 0 else 0.0

        start_node = pipe.start_node_name
        end_node = pipe.end_node_name
        h_start = (
            float(sim_res.node["head"].loc[0, start_node])
            if hasattr(sim_res, "node") and "head" in sim_res.node and start_node in sim_res.node["head"].columns
            else 0.0
        )
        h_end = (
            float(sim_res.node["head"].loc[0, end_node])
            if hasattr(sim_res, "node") and "head" in sim_res.node and end_node in sim_res.node["head"].columns
            else 0.0
        )
        length_m = float(getattr(pipe, "length", 1.0) or 1.0)
        hl_km = abs(h_start - h_end) / max(length_m / 1000.0, 1e-3)

        rc = get_road_class(engine, pid, wn)

        if v > 2.5:
            warnings.append(
                PipeWarning(
                    pipe_id=pid,
                    warning_type="high_velocity",
                    value=round(v, 2),
                    unit="m/s",
                    message=f"High velocity in pipe {pid}: {v:.2f} m/s (> 2.5 m/s)",
                )
            )
        elif 0.0 < v < 0.15 and rc in ("primary", "feeder"):
            warnings.append(
                PipeWarning(
                    pipe_id=pid,
                    warning_type="low_velocity",
                    value=round(v, 2),
                    unit="m/s",
                    message=f"Low velocity in {rc} pipe {pid}: {v:.2f} m/s (< 0.15 m/s)",
                )
            )

        if hl_km > 15.0:
            warnings.append(
                PipeWarning(
                    pipe_id=pid,
                    warning_type="high_headloss",
                    value=round(hl_km, 2),
                    unit="m/km",
                    message=f"High headloss gradient in pipe {pid}: {hl_km:.2f} m/km (> 15.0 m/km)",
                )
            )

    return warnings


def compute_metrics_from_wn(
    engine: Any,
    wn_copy: wntr.network.WaterNetworkModel,
    leaks: Optional[List[Any]] = None,
    raise_on_error: bool = False,
) -> tuple[WhatIfMetrics, dict[str, float], list[PipeWarning], bool, str]:
    """
    Run steady-state PDD simulation on a cloned network model and extract WhatIfMetrics.

    Returns:
        (metrics, scenario_pressures_m, pipe_warnings, converged, solver_message)
    """
    # Configure PDD steady-state invariants
    wn_copy.options.time.duration = 0
    wn_copy.options.hydraulic.demand_model = "PDD"
    wn_copy.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
    wn_copy.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
    wn_copy.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

    # Accumulate leaks per node_id
    leak_map: dict[str, float] = {}
    if leaks:
        for lk in leaks:
            if isinstance(lk, WhatIfLeakInjection):
                nid = lk.node_id
                area_cm2 = lk.leak_area_cm2
            elif isinstance(lk, dict):
                nid = lk["node_id"]
                area_cm2 = float(lk.get("leak_area_cm2", 25.0))
            else:
                nid = getattr(lk, "node_id")
                area_cm2 = float(getattr(lk, "leak_area_cm2", 25.0))
            leak_map[nid] = leak_map.get(nid, 0.0) + (area_cm2 / 10000.0)

        for nid, total_area_m2 in leak_map.items():
            if nid in wn_copy.junction_name_list:
                node = wn_copy.get_node(nid)
                node.add_leak(wn_copy, area=total_area_m2, discharge_coeff=DISCHARGE_COEFF, start_time=0)

    # Topology analysis for cut-off nodes
    g = get_open_links_graph(wn_copy)
    junctions = (
        list(getattr(engine, "junctions", []))
        if hasattr(engine, "junctions") and engine.junctions
        else list(wn_copy.junction_name_list)
    )
    cut_off = find_cut_off_junctions(wn_copy, g, junctions)

    # Solve simulation
    try:
        sim = wntr.sim.WNTRSimulator(wn_copy)
        res = sim.run_sim(convergence_error=True)
        if getattr(res, "error_code", None) is not None:
            err_msg = f"WNTRSimulator returned error_code {res.error_code}"
            if raise_on_error:
                raise HTTPException(status_code=422, detail=f"Scenario solver failed to converge: {err_msg}")
            empty_m = WhatIfMetrics(
                min_pressure_m=0.0,
                avg_pressure_m=0.0,
                max_pressure_m=0.0,
                pressure_deficit_nodes=len(junctions),
                unsupplied_nodes=sorted(junctions, key=_natural_key),
                affected_households=sum(get_households(engine, j) for j in junctions),
                total_leak_loss_lpm=0.0,
                tankers_lost_per_day=0.0,
            )
            return empty_m, {}, [], False, err_msg
    except HTTPException:
        raise
    except Exception as exc:
        if raise_on_error:
            raise HTTPException(status_code=422, detail=f"Scenario solver failed to converge: {exc}")
        empty_m = WhatIfMetrics(
            min_pressure_m=0.0,
            avg_pressure_m=0.0,
            max_pressure_m=0.0,
            pressure_deficit_nodes=len(junctions),
            unsupplied_nodes=sorted(junctions, key=_natural_key),
            affected_households=sum(get_households(engine, j) for j in junctions),
            total_leak_loss_lpm=0.0,
            tankers_lost_per_day=0.0,
        )
        return empty_m, {}, [], False, str(exc)

    # Extract junction pressures
    pressure_df = res.node.get("pressure")
    scenario_pressures: dict[str, float] = {}
    for j in junctions:
        if j in cut_off:
            scenario_pressures[j] = 0.0
        else:
            raw_p = (
                float(pressure_df[j].iloc[0])
                if (pressure_df is not None and j in pressure_df.columns)
                else 0.0
            )
            scenario_pressures[j] = max(0.0, raw_p)

    # Extract leak rates
    total_lpm = 0.0
    if leak_map and "leak_demand" in res.node:
        leak_df = res.node["leak_demand"]
        for nid in leak_map.keys():
            if nid in leak_df.columns:
                flow_m3s = float(leak_df[nid].iloc[0])
                total_lpm += max(0.0, flow_m3s) * 60000.0

    total_leak_loss_lpm = round(total_lpm, 2)
    tankers_lost_per_day = lpm_to_tankers_per_day(total_lpm)

    # Metrics calculation
    p_values = list(scenario_pressures.values())
    min_p = round(min(p_values), 2) if p_values else 0.0
    avg_p = round(sum(p_values) / len(p_values), 2) if p_values else 0.0
    max_p = round(max(p_values), 2) if p_values else 0.0
    deficit_count = sum(1 for p in p_values if p < REQUIRED_PRESSURE_M)

    unsupplied_nodes = sorted(
        [j for j in junctions if j in cut_off or scenario_pressures[j] <= MINIMUM_PRESSURE_M],
        key=_natural_key,
    )
    affected_hh = sum(get_households(engine, j) for j in unsupplied_nodes)

    metrics = WhatIfMetrics(
        min_pressure_m=min_p,
        avg_pressure_m=avg_p,
        max_pressure_m=max_p,
        pressure_deficit_nodes=deficit_count,
        unsupplied_nodes=unsupplied_nodes,
        affected_households=affected_hh,
        total_leak_loss_lpm=total_leak_loss_lpm,
        tankers_lost_per_day=tankers_lost_per_day,
    )

    # Pipe warnings
    pipe_warnings = compute_pipe_warnings(engine, wn_copy, res)

    return metrics, scenario_pressures, pipe_warnings, True, "Scenario solved successfully"


def evaluate_what_if(engine: Any, req: WhatIfRequest) -> WhatIfResponse:
    """
    Evaluate a counterfactual What-If scenario against the hydraulic engine.

    1. Validates revision token (HTTP 409 on mismatch).
    2. Validates pipe and junction IDs (HTTP 404 on unknown IDs).
    3. Clones the network model (engine.wn is never mutated).
    4. Computes baseline metrics from untouched baseline.
    5. Applies overrides, demand multipliers, head adjustments, and leak injections.
    6. Solves steady-state PDD simulation and checks convergence (HTTP 422 on failure).
    7. Computes scenario metrics, pressure deltas, and pipe warnings.
    """
    t0 = time.perf_counter()

    if isinstance(req, dict):
        req = WhatIfRequest.model_validate(req)

    # 1. Revision check
    active_rev = getattr(engine, "network_revision", None)
    if req.expected_network_revision is not None and req.expected_network_revision != active_rev:
        raise HTTPException(
            status_code=409,
            detail=f"Stale network_revision: expected {req.expected_network_revision}, active is {active_rev}",
        )

    # 2. Validate IDs before solving
    known_links = set(engine.wn.link_name_list) if hasattr(engine, "wn") else set()
    for ov in req.pipe_overrides:
        if ov.pipe_id not in known_links:
            raise HTTPException(status_code=404, detail=f"Unknown pipe_id: {ov.pipe_id}")

    known_junctions = set(getattr(engine, "junctions", []))
    if not known_junctions and hasattr(engine, "wn"):
        known_junctions = set(engine.wn.junction_name_list)
    for lk in req.leaks:
        if lk.node_id not in known_junctions:
            raise HTTPException(status_code=404, detail=f"Unknown junction node_id: {lk.node_id}")

    # Set optional seed
    if req.seed is not None:
        random.seed(req.seed)
        np.random.seed(req.seed)

    # 3. Clone network (never mutate engine.wn!)
    if hasattr(engine, "clone_for_scenario") and callable(engine.clone_for_scenario):
        clone_res = engine.clone_for_scenario()
        wn = clone_res[0] if isinstance(clone_res, tuple) else clone_res
    else:
        wn = copy.deepcopy(engine.wn)

    # 4. Compute baseline metrics from engine's untouched baseline
    baseline_metrics = compute_baseline_metrics(engine)

    # 5. Apply scenario modifications to cloned wn
    # Pipe overrides
    for ov in req.pipe_overrides:
        link = wn.get_link(ov.pipe_id)
        if ov.status == "CLOSED":
            link.initial_status = wntr.network.LinkStatus.Closed
        elif ov.status == "OPEN":
            link.initial_status = wntr.network.LinkStatus.Opened

        if ov.diameter_mm is not None:
            link.diameter = float(ov.diameter_mm) / 1000.0
        if ov.roughness is not None:
            link.roughness = float(ov.roughness)

    # Demand multiplier
    if req.demand_multiplier != 1.0:
        for j_name in wn.junction_name_list:
            j = wn.get_node(j_name)
            if getattr(j, "demand_timeseries_list", None):
                for ts in j.demand_timeseries_list:
                    ts.base_value *= req.demand_multiplier
            elif hasattr(j, "base_demand"):
                j.base_demand *= req.demand_multiplier

    # Reservoir head delta
    if req.reservoir_head_delta_m != 0.0:
        for r_name in wn.reservoir_name_list:
            r = wn.get_node(r_name)
            r.base_head += req.reservoir_head_delta_m

    # 6. Solve scenario & extract metrics (leaks applied inside compute_metrics_from_wn)
    scenario_metrics, scenario_pressures, pipe_warnings, converged, solver_msg = compute_metrics_from_wn(
        engine,
        wn,
        leaks=req.leaks,
        raise_on_error=True,
    )

    # 7. Compute deltas & format node pressures
    junctions = (
        list(getattr(engine, "junctions", []))
        if hasattr(engine, "junctions") and engine.junctions
        else list(wn.junction_name_list)
    )
    baseline_p = getattr(engine, "baseline_pressure", {}) or {}

    node_pressure_deltas: dict[str, float] = {
        j: round(scenario_pressures.get(j, 0.0) - float(baseline_p.get(j, 0.0)), 2)
        for j in junctions
    }
    scenario_node_pressures: dict[str, float] = {
        j: round(scenario_pressures.get(j, 0.0), 2)
        for j in junctions
    }

    compute_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return WhatIfResponse(
        network_revision=str(active_rev or "rev-unknown"),
        converged=converged,
        solver_message=solver_msg,
        baseline=baseline_metrics,
        scenario=scenario_metrics,
        node_pressure_deltas_m=node_pressure_deltas,
        scenario_node_pressures_m=scenario_node_pressures,
        pipe_warnings=pipe_warnings,
        compute_time_ms=compute_time_ms,
        limitations=LIMITATIONS,
    )


class ScenarioEngine:
    """Service wrapper for scenario evaluation."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def evaluate_what_if(self, req: WhatIfRequest) -> WhatIfResponse:
        return evaluate_what_if(self.engine, req)

    def compute_baseline_metrics(self) -> WhatIfMetrics:
        return compute_baseline_metrics(self.engine)

    def compute_metrics_from_wn(
        self,
        wn_copy: wntr.network.WaterNetworkModel,
        leaks: Optional[List[Any]] = None,
        raise_on_error: bool = False,
    ) -> tuple[WhatIfMetrics, dict[str, float], list[PipeWarning], bool, str]:
        return compute_metrics_from_wn(self.engine, wn_copy, leaks=leaks, raise_on_error=raise_on_error)
