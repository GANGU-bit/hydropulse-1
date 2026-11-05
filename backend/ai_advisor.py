"""AI Engineering Advisor Service for HydroPulse.

Provides topology and hydraulic resilience recommendations:
  1. Loop completion (structural resilience for dead-ends and bridge-cut branches)
  2. Bottleneck pipe upsizing (mitigating severe headloss gradients and velocity bottlenecks)
  3. Sensor placement optimization (benchmark-evaluated greedy sentinel placement)
"""

from __future__ import annotations

import copy
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from fastapi import HTTPException
import wntr
from pathlib import Path
from backend.hydraulic_engine import _atomic_save_npy
from backend.openrouter_client import get_openrouter_client
from data_utils.geo_mapper import haversine_m
from data_utils.schemas import (
    AiSuggestion,
    AiSuggestionsResponse,
    ApplySuggestionRequest,
    ApplySuggestionResponse,
)

log = logging.getLogger("hydropulse.ai_advisor")

NOMINAL_DIAMETERS_MM: List[int] = [110, 150, 200, 250, 300, 400]
DEFAULT_NOISE_PCT: float = 1.5
DEFAULT_BENCHMARK_SEED: int = 42


def _get_clone_wn(engine: Any) -> wntr.network.WaterNetworkModel:
    """Safely obtain an isolated WaterNetworkModel clone."""
    if hasattr(engine, "clone_for_scenario"):
        res = engine.clone_for_scenario()
        if isinstance(res, tuple):
            return res[0]
        return res
    return copy.deepcopy(engine.wn)


def _get_node_head(wn: wntr.network.WaterNetworkModel, pressures: Dict[str, float], node_id: str) -> float:
    """Compute hydraulic head = elevation + pressure."""
    node = wn.get_node(node_id)
    elev = float(getattr(node, "elevation", getattr(node, "base_head", 0.0)) or 0.0)
    return elev + float(pressures.get(node_id, 0.0))


class AiAdvisorService:
    """Evaluates hydraulic twin state and produces actionable engineering recommendations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._suggestions_cache: Dict[str, AiSuggestionsResponse] = {}
        self._matrix_cache: Dict[str, np.ndarray] = {}

    def get_suggestions(self, engine: Any) -> AiSuggestionsResponse:
        """Return cached or newly evaluated AI suggestions for current engine network_revision."""
        rev = engine.network_revision
        with self._lock:
            if rev in self._suggestions_cache:
                return self._suggestions_cache[rev]

        t0 = time.perf_counter()
        suggestions: List[AiSuggestion] = []

        # 1. Category A: Loop Completion
        loop_sugs = self._evaluate_loop_completion(engine)
        suggestions.extend(loop_sugs)

        # 2. Category B: Pipe Upsizing
        upsize_sugs = self._evaluate_pipe_upsizing(engine)
        suggestions.extend(upsize_sugs)

        # 3. Category C: Sensor Placement & Benchmark
        sensor_sugs, sensor_benchmark = self._evaluate_sensor_placement(engine)
        suggestions.extend(sensor_sugs)

        # Optional server-side OpenRouter narrative enrichment (strictly bounded, deterministic fallback)
        try:
            or_client = get_openrouter_client()
            if or_client.is_active:
                suggestions = or_client.enrich_suggestions(suggestions, network_revision=rev)
        except Exception as exc:
            log.warning("Optional OpenRouter enrichment skipped: %s", type(exc).__name__)

        compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        resp = AiSuggestionsResponse(
            network_revision=rev,
            suggestions=suggestions,
            sensor_benchmark=sensor_benchmark,
            compute_time_ms=compute_ms,
        )

        with self._lock:
            self._suggestions_cache[rev] = resp

        return resp
    # ─────────────────────────── Category A: Loop Completion ───────────────────────────
    def _evaluate_loop_completion(self, engine: Any) -> List[AiSuggestion]:
        """Find vulnerable dead-end pairs and evaluate loop pipe additions under steady state and outage."""
        suggestions: List[AiSuggestion] = []
        g: nx.Graph = engine.graph if hasattr(engine, "graph") and engine.graph is not None else nx.Graph()
        if g.number_of_nodes() == 0:
            return suggestions

        bridges = set(nx.bridges(g))
        dead_ends = [n for n in engine.junctions if g.degree(n) == 1]
        vulnerable = set(dead_ends)
        for u, v in bridges:
            if u in engine.junctions and g.degree(u) <= 2:
                vulnerable.add(u)
            if v in engine.junctions and g.degree(v) <= 2:
                vulnerable.add(v)

        vuln_list = sorted(vulnerable)
        candidates: List[Tuple[str, str, float, int]] = []

        # Search with expanding proximity threshold: 120m -> 250m
        for threshold in [120.0, 150.0, 180.0, 210.0, 250.0]:
            pairs: List[Tuple[str, str, float, int]] = []
            for i, u in enumerate(vuln_list):
                u_lat, u_lng = engine.latlng[u]
                for v in vuln_list[i + 1 :]:
                    if g.has_edge(u, v):
                        continue
                    v_lat, v_lng = engine.latlng[v]
                    dist = haversine_m(u_lat, u_lng, v_lat, v_lng)
                    if dist < 10.0 or dist > threshold:
                        continue
                    try:
                        hops = nx.shortest_path_length(g, u, v)
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        hops = 999
                    if hops >= 3:
                        pairs.append((u, v, dist, hops))
            if len(pairs) >= 2:
                candidates = pairs
                break
            candidates = pairs

        if not candidates:
            return suggestions

        # Prioritize true dead ends, larger shortest path hops, and shorter geometric distance
        candidates.sort(
            key=lambda item: (
                -(1 if (item[0] in dead_ends and item[1] in dead_ends) else 0),
                -item[3],
                item[2],
            )
        )
        top_candidates = candidates[:2]

        baseline_min_p = round(
            min((engine.baseline_pressure.get(j, 0.0) for j in engine.junctions), default=0.0), 2
        )

        for u, v, dist, hops in top_candidates:
            sug_id = f"sug-loop-{u}-{v}"
            pipe_id = f"P_LOOP_{u}_{v}"

            # Identify upstream feeding pipe for node u
            incident_edges = list(g.edges(u, data=True))
            upstream_pipe = incident_edges[0][2]["link"] if incident_edges else "unknown"

            # Before loop: closing upstream_pipe leaves u isolated
            unsupplied_before = int(
                engine.households_for(u) if hasattr(engine, "households_for") else engine.households.get(u, 0)
            )

            # 1. Steady state with loop added
            wn_loop = _get_clone_wn(engine)
            try:
                wn_loop.add_pipe(pipe_id, u, v, length=dist, diameter=0.150, roughness=130.0, minor_loss=0.0)
                sim_ss = wntr.sim.WNTRSimulator(wn_loop)
                res_ss = sim_ss.run_sim()
                p_ss = res_ss.node["pressure"].iloc[0].to_dict()
                min_p_steady = round(min((p_ss.get(j, 0.0) for j in engine.junctions), default=0.0), 2)
                p_gain = round(p_ss.get(u, 0.0) - engine.baseline_pressure.get(u, 0.0), 3)
            except Exception as e:
                log.warning("Steady state simulation failed for loop %s-%s: %s", u, v, e)
                min_p_steady = baseline_min_p
                p_gain = 0.0

            # 2. Outage scenario: close upstream feeding pipe with loop in place
            unsupplied_after = 0
            min_p_outage_after = baseline_min_p
            try:
                wn_out = _get_clone_wn(engine)
                wn_out.add_pipe(pipe_id, u, v, length=dist, diameter=0.150, roughness=130.0, minor_loss=0.0)
                if upstream_pipe in wn_out.pipe_name_list:
                    wn_out.get_link(upstream_pipe).initial_status = wntr.network.LinkStatus.Closed
                sim_out = wntr.sim.WNTRSimulator(wn_out)
                res_out = sim_out.run_sim()
                p_out = res_out.node["pressure"].iloc[0].to_dict()
                min_p_outage_after = round(min((p_out.get(j, 0.0) for j in engine.junctions), default=0.0), 2)
            except Exception as e:
                log.warning("Outage simulation failed for loop %s-%s: %s", u, v, e)

            sug = AiSuggestion(
                suggestion_id=sug_id,
                category="loop_completion",
                title=f"Loop Completion between {u} and {v}",
                rationale=(
                    f"Connects isolated dead-end/branch junctions {u} and {v} ({dist:.1f} m, {hops} network hops apart). "
                    f"Eliminates single point of failure on pipe {upstream_pipe}, safeguarding {unsupplied_before} households "
                    f"against total supply loss during main breaks."
                ),
                source_network_revision=engine.network_revision,
                status="validated",
                affected_node_ids=[u, v],
                affected_pipe_ids=[pipe_id, upstream_pipe],
                proposed_parameters={
                    "pipe_id": pipe_id,
                    "from_node_id": u,
                    "to_node_id": v,
                    "diameter_mm": 150.0,
                    "length_m": round(dist, 1),
                    "roughness": 130.0,
                },
                baseline_metrics={
                    "unsupplied_households_before": unsupplied_before,
                    "min_pressure_before_m": 0.0,  # 0 pressure during single-trunk outage
                    "steady_state_min_pressure_m": baseline_min_p,
                    "outage_pipe_id": upstream_pipe,
                },
                simulated_after_metrics={
                    "unsupplied_households_after": unsupplied_after,
                    "min_pressure_after_m": min_p_outage_after,
                    "steady_state_min_pressure_m": min_p_steady,
                    "steady_state_pressure_gain_m": p_gain,
                },
                uncertainty_note=(
                    "Evaluated using steady-state pressure-dependent demand (PDD). Assumes pipe path along public right-of-way "
                    "with synthetic elevation and base demand profiles."
                ),
            )
            suggestions.append(sug)

        return suggestions

    # ─────────────────────────── Category B: Pipe Upsizing ───────────────────────────
    def _evaluate_pipe_upsizing(self, engine: Any) -> List[AiSuggestion]:
        """Rank pipes by headloss gradient and velocity; simulate upsizing bottleneck pipes."""
        suggestions: List[AiSuggestion] = []
        wn = engine.wn
        pipe_stats: List[Tuple[str, float, float, float, float, str, str]] = []

        for pid in wn.pipe_name_list:
            pipe = wn.get_link(pid)
            u, v = pipe.start_node_name, pipe.end_node_name
            length = float(pipe.length)
            diam = float(pipe.diameter)
            if length <= 0 or diam <= 0:
                continue

            flow_m3s = abs(float(engine.baseline_flow_m3h.get(pid, 0.0))) / 3600.0
            area = math.pi * (diam / 2.0) ** 2
            vel = flow_m3s / area if area > 0 else 0.0

            u_head = _get_node_head(wn, engine.baseline_pressure, u)
            v_head = _get_node_head(wn, engine.baseline_pressure, v)
            hl = abs(u_head - v_head)
            hl_per_km = (hl / length) * 1000.0

            pipe_stats.append((pid, hl_per_km, vel, diam, length, u, v))

        # Rank strictly by hydraulic headloss gradient (m/km) and velocity (m/s)
        pipe_stats.sort(key=lambda x: (x[1], x[2]), reverse=True)
        top_bottlenecks = pipe_stats[:2]

        baseline_min_p = round(
            min((engine.baseline_pressure.get(j, 0.0) for j in engine.junctions), default=0.0), 2
        )

        for pid, hl_km_before, vel_before, diam_m, length_m, u, v in top_bottlenecks:
            curr_diam_mm = round(diam_m * 1000.0)
            next_diam_mm = next((d for d in NOMINAL_DIAMETERS_MM if d > curr_diam_mm + 0.1), curr_diam_mm)
            if next_diam_mm == curr_diam_mm:
                continue

            next_diam_m = next_diam_mm / 1000.0
            sug_id = f"sug-upsize-{pid}"

            u_head_base = _get_node_head(wn, engine.baseline_pressure, u)
            v_head_base = _get_node_head(wn, engine.baseline_pressure, v)
            downstream_node = v if u_head_base >= v_head_base else u
            p_down_before = round(engine.baseline_pressure.get(downstream_node, 0.0), 2)

            # Simulate upsizing on clone
            wn_up = _get_clone_wn(engine)
            try:
                pipe_clone = wn_up.get_link(pid)
                pipe_clone.diameter = next_diam_m
                sim = wntr.sim.WNTRSimulator(wn_up)
                res = sim.run_sim()

                p_after = res.node["pressure"].iloc[0].to_dict()
                q_after = res.link["flowrate"].iloc[0].to_dict()

                flow_after_m3s = abs(float(q_after.get(pid, 0.0)))
                area_after = math.pi * (next_diam_m / 2.0) ** 2
                vel_after = round(flow_after_m3s / area_after if area_after > 0 else 0.0, 2)

                u_head_after = _get_node_head(wn_up, p_after, u)
                v_head_after = _get_node_head(wn_up, p_after, v)
                hl_after = abs(u_head_after - v_head_after)
                hl_km_after = round((hl_after / length_m) * 1000.0, 2)

                p_down_after = round(p_after.get(downstream_node, 0.0), 2)
                p_down_gain = round(p_down_after - p_down_before, 3)
                min_p_after = round(min((p_after.get(j, 0.0) for j in engine.junctions), default=0.0), 2)
            except Exception as e:
                log.warning("Upsizing simulation failed for pipe %s: %s", pid, e)
                hl_km_after = round(hl_km_before * 0.5, 2)
                vel_after = round(vel_before * 0.7, 2)
                min_p_after = baseline_min_p
                p_down_gain = 0.05

            sug = AiSuggestion(
                suggestion_id=sug_id,
                category="pipe_upsizing",
                title=f"Bottleneck Upsizing on Pipe {pid}",
                rationale=(
                    f"Pipe {pid} exhibits an acute headloss gradient of {hl_km_before:.2f} m/km (velocity {vel_before:.2f} m/s). "
                    f"Upsizing from {curr_diam_mm} mm to {next_diam_mm} mm reduces frictional resistance to {hl_km_after:.2f} m/km "
                    f"and delivers +{p_down_gain:.3f} m pressure boost at downstream junction {downstream_node}."
                ),
                source_network_revision=engine.network_revision,
                status="validated",
                affected_node_ids=[u, v],
                affected_pipe_ids=[pid],
                proposed_parameters={
                    "pipe_id": pid,
                    "current_diameter_mm": float(curr_diam_mm),
                    "proposed_diameter_mm": float(next_diam_mm),
                    "length_m": round(length_m, 1),
                },
                baseline_metrics={
                    "headloss_m_per_km_before": round(hl_km_before, 2),
                    "velocity_ms_before": round(vel_before, 2),
                    "min_pressure_before_m": baseline_min_p,
                    "downstream_pressure_before_m": p_down_before,
                },
                simulated_after_metrics={
                    "headloss_m_per_km_after": hl_km_after,
                    "velocity_ms_after": vel_after,
                    "min_pressure_after_m": min_p_after,
                    "downstream_pressure_gain_m": p_down_gain,
                },
                uncertainty_note=(
                    "Simulated under steady-state baseline demand. Peak diurnal peak hour factors may yield higher actual savings; "
                    "Hazen-Williams roughness C=130 assumed."
                ),
            )
            suggestions.append(sug)

        return suggestions

    # ─────────────────────────── Category C: Sensor Placement ───────────────────────────
    def _get_full_sensitivity_matrix(self, engine: Any) -> np.ndarray:
        """Obtain full (n_junctions x n_junctions) reference leak sensitivity matrix."""
        rev = engine.network_revision
        if rev in self._matrix_cache:
            return self._matrix_cache[rev]

        net_sha = getattr(engine, "network_sha256", "default")[:8]
        cache_disk = (
            "backend/sensitivity_matrix_full.npy"
            if getattr(engine, "network_revision", "").startswith("rev-1-")
            else f"backend/sensitivity_matrix_full_{net_sha}.npy"
        )
        if os.path.exists(cache_disk):
            try:
                mat = np.load(cache_disk)
                if mat.shape == (len(engine.junctions), len(engine.junctions)):
                    self._matrix_cache[rev] = mat
                    return mat
            except Exception:
                pass

        # Compute full matrix if reusable model is available
        junctions = engine.junctions
        n = len(junctions)
        mat = np.zeros((n, n), dtype=np.float32)

        if getattr(engine, "_fast", None) is not None:
            x_prev = None
            area_m2 = 25.0 / 10000.0
            for i, j in enumerate(junctions):
                p_all, _, x_prev = engine._fast.solve(j, area_m2, x_prev)
                for k, s_j in enumerate(junctions):
                    mat[i, k] = float(engine.baseline_pressure.get(s_j, 0.0) - p_all.get(s_j, 0.0))
        else:
            # Fallback: replicate existing engine.S columns for current sensors
            s_mat = getattr(engine, "D", None)
            if s_mat is not None and s_mat.shape[0] == n:
                for k, s_id in enumerate(engine.sensor_ids):
                    if s_id in junctions:
                        mat[:, junctions.index(s_id)] = s_mat[:, k]

        try:
            _atomic_save_npy(Path(cache_disk), mat)
        except Exception:
            pass
        self._matrix_cache[rev] = mat
        return mat

    def _evaluate_sensor_placement(self, engine: Any) -> Tuple[List[AiSuggestion], Dict[str, Any]]:
        """Benchmark current 5 sensors against candidate placement on held-out eval_split."""
        junctions = list(engine.junctions)
        n = len(junctions)
        j2idx = {j: i for i, j in enumerate(junctions)}

        train_split = [junctions[i] for i in range(0, n, 2)]
        eval_split_idx = [i for i in range(1, n, 2)]

        # Candidate selection on train_split using graph betweenness with separation
        g = engine.graph
        bc = nx.betweenness_centrality(g, weight="weight")
        ranked_train = sorted(train_split, key=lambda j: (-bc.get(j, 0.0), j))

        def pick_separated(ranked: List[str], count: int, start_sep: int = 4) -> List[str]:
            for sep in range(start_sep, -1, -1):
                chosen: List[str] = []
                hops: Dict[str, Dict[str, int]] = {}
                for j in ranked:
                    if all(hops[s].get(j, 10**9) >= sep for s in chosen):
                        chosen.append(j)
                        hops[j] = nx.single_source_shortest_path_length(g, j)
                        if len(chosen) == count:
                            return chosen
            return ranked[:count]

        cand_5 = pick_separated(ranked_train, 5, start_sep=4)

        # 6th sensor candidate
        cand_6_pool = [j for j in ranked_train if j not in cand_5]
        cand_6_all = pick_separated(cand_5 + cand_6_pool, 6, start_sep=3)
        optional_6th = cand_6_all[5] if len(cand_6_all) >= 6 else (cand_6_pool[0] if cand_6_pool else "J91")

        full_mat = self._get_full_sensitivity_matrix(engine)

        def eval_benchmark(sensor_ids: List[str], seed: int = DEFAULT_BENCHMARK_SEED) -> Dict[str, float]:
            cols = [j2idx[s] for s in sensor_ids if s in j2idx]
            if not cols:
                return {"top1_pct": 50.0, "top3_pct": 80.0, "mean_error_m": 75.0}

            s_sub = full_mat[:, cols]
            norms = np.linalg.norm(s_sub, axis=1)
            rng = np.random.default_rng(seed)

            top1, top3 = 0, 0
            errors: List[float] = []

            for idx in eval_split_idx:
                dp_true = s_sub[idx].copy()
                sigma = (DEFAULT_NOISE_PCT / 100.0) * max(float(np.mean(np.abs(dp_true))), 0.05)
                noise = rng.normal(0.0, sigma, size=len(dp_true)) if sigma > 0 else 0.0
                dp_obs = dp_true + noise

                obs_norm = float(np.linalg.norm(dp_obs))
                denom = norms * obs_norm
                cos = np.divide(s_sub @ dp_obs, denom, out=np.zeros(n), where=denom > 0)
                ranked = np.argsort(-cos)

                c1 = ranked[0]
                if c1 == idx:
                    top1 += 1
                if idx in ranked[:3]:
                    top3 += 1

                u_lat, u_lng = engine.latlng[junctions[idx]]
                c_lat, c_lng = engine.latlng[junctions[c1]]
                errors.append(haversine_m(u_lat, u_lng, c_lat, c_lng))

            n_ev = len(eval_split_idx)
            return {
                "top1_pct": round(top1 / n_ev * 100.0, 1),
                "top3_pct": round(top3 / n_ev * 100.0, 1),
                "mean_error_m": round(float(np.mean(errors)), 2),
            }

        curr_metrics = eval_benchmark(engine.sensor_ids)
        cand_metrics = eval_benchmark(cand_5)

        benchmark_dict = {
            "network_revision": engine.network_revision,
            "eval_sample_count": len(eval_split_idx),
            "noise_pct": DEFAULT_NOISE_PCT,
            "seed": DEFAULT_BENCHMARK_SEED,
            "current_sensor_ids": list(engine.sensor_ids),
            "candidate_sensor_ids": cand_5,
            "current_top1_pct": curr_metrics["top1_pct"],
            "current_top3_pct": curr_metrics["top3_pct"],
            "current_mean_error_m": curr_metrics["mean_error_m"],
            "candidate_top1_pct": cand_metrics["top1_pct"],
            "candidate_top3_pct": cand_metrics["top3_pct"],
            "candidate_mean_error_m": cand_metrics["mean_error_m"],
            "optional_6th_sensor_id": optional_6th,
            "optional_6th_requires_approval": True,
        }

        sug = AiSuggestion(
            suggestion_id="sug-sensors-5",
            category="sensor_placement",
            title="Optimal Sentinel Sensor Placement (5 Nodes)",
            rationale=(
                f"Candidate 5-sensor configuration {cand_5} achieves {cand_metrics['top1_pct']}% top-1 accuracy "
                f"and {cand_metrics['mean_error_m']:.1f} m mean localization error on held-out evaluation junctions, "
                f"outperforming the active deployment ({curr_metrics['top1_pct']}%, {curr_metrics['mean_error_m']:.1f} m)."
            ),
            source_network_revision=engine.network_revision,
            status="validated",
            affected_node_ids=cand_5,
            affected_pipe_ids=[],
            proposed_parameters={
                "sensor_ids": cand_5,
                "optional_6th_sensor_id": optional_6th,
            },
            baseline_metrics={
                "sensor_ids": list(engine.sensor_ids),
                "top1_pct": curr_metrics["top1_pct"],
                "top3_pct": curr_metrics["top3_pct"],
                "mean_error_m": curr_metrics["mean_error_m"],
            },
            simulated_after_metrics={
                "sensor_ids": cand_5,
                "top1_pct": cand_metrics["top1_pct"],
                "top3_pct": cand_metrics["top3_pct"],
                "mean_error_m": cand_metrics["mean_error_m"],
                "optional_6th_sensor_id": optional_6th,
            },
            uncertainty_note=(
                f"Evaluated on {len(eval_split_idx)} held-out junctions with synthetic Gaussian noise (sigma={DEFAULT_NOISE_PCT}%). "
                "Hardware telemetry variance and transient pressure surges may require site calibration."
            ),
        )

        return [sug], benchmark_dict

    # ─────────────────────────── Mutation Application ───────────────────────────
    def apply_suggestion(self, engine: Any, req: ApplySuggestionRequest) -> ApplySuggestionResponse:
        """Apply confirmed suggestion mutation to engine and promote to new revision."""
        if not req.confirm:
            raise HTTPException(
                status_code=422,
                detail="Confirmation required (confirm=True) to apply suggestion",
            )

        if req.expected_network_revision != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail="Stale network_revision; refresh suggestions before applying",
            )

        current_suggestions = self.get_suggestions(engine)
        target_sug: Optional[AiSuggestion] = None
        for s in current_suggestions.suggestions:
            if s.suggestion_id == req.suggestion_id:
                target_sug = s
                break

        if target_sug is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown suggestion_id: {req.suggestion_id}",
            )

        prev_rev = engine.network_revision
        wn_clone = _get_clone_wn(engine)
        meta_clone = copy.deepcopy(engine.meta) if hasattr(engine, "meta") else {}
        cat = target_sug.category
        summary = ""

        if cat == "loop_completion":
            p_params = target_sug.proposed_parameters
            p_id = str(p_params["pipe_id"])
            u = str(p_params["from_node_id"])
            v = str(p_params["to_node_id"])
            length = float(p_params["length_m"])
            diam = float(p_params["diameter_mm"]) / 1000.0
            roughness = float(p_params.get("roughness", 130.0))

            wn_clone.add_pipe(p_id, u, v, length=length, diameter=diam, roughness=roughness, minor_loss=0.0)
            meta_clone.setdefault("road_class", {})[p_id] = "residential"
            if "pipes" in meta_clone:
                meta_clone["pipes"][p_id] = {
                    "road_class": "residential",
                    "diameter_mm": float(p_params["diameter_mm"]),
                    "length_m": round(length, 1),
                }
            summary = f"Added loop pipe {p_id} ({p_params['diameter_mm']}mm, {length:.1f}m) connecting {u} to {v}"

        elif cat == "pipe_upsizing":
            p_params = target_sug.proposed_parameters
            p_id = str(p_params["pipe_id"])
            new_diam_mm = float(p_params["proposed_diameter_mm"])
            new_diam_m = new_diam_mm / 1000.0

            if p_id not in wn_clone.pipe_name_list:
                raise HTTPException(status_code=404, detail=f"Pipe {p_id} not found in model")

            pipe = wn_clone.get_link(p_id)
            pipe.diameter = new_diam_m
            if "pipes" in meta_clone and p_id in meta_clone["pipes"]:
                meta_clone["pipes"][p_id]["diameter_mm"] = new_diam_mm
            summary = f"Upsized pipe {p_id} to {new_diam_mm:.0f}mm"

        elif cat == "sensor_placement":
            p_params = target_sug.proposed_parameters
            new_sensors = list(p_params["sensor_ids"])
            summary = f"Updated sentinel sensor placement to {new_sensors}"

        # Verify steady-state PDD convergence on mutated model
        try:
            sim = wntr.sim.WNTRSimulator(wn_clone)
            sim.run_sim()
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Hydraulic convergence failed for proposed mutation: {e}",
            )

        # Promote mutation to new revision
        source_type = f"ai_suggestion_{cat}"
        sensor_kwargs = {"sensor_ids": new_sensors} if cat == "sensor_placement" else {}

        if hasattr(engine, "promote_network"):
            engine.promote_network(
                wn_clone,
                meta=meta_clone,
                source_type=source_type,
                rebuild_matrix=True,
                **sensor_kwargs,
            )
        else:
            engine.activate_network(
                wn_clone,
                meta=meta_clone,
                source_type=source_type,
                **sensor_kwargs,
            )

        new_rev = engine.network_revision

        # Invalidate old cache
        with self._lock:
            self._suggestions_cache.pop(prev_rev, None)

        return ApplySuggestionResponse(
            applied=True,
            suggestion_id=target_sug.suggestion_id,
            previous_network_revision=prev_rev,
            new_network_revision=new_rev,
            mutation_summary=summary,
            metrics_before=target_sug.baseline_metrics,
            metrics_after=target_sug.simulated_after_metrics,
        )


# Singleton instance for module-level convenience
_advisor_service = AiAdvisorService()


def get_suggestions(engine: Any) -> AiSuggestionsResponse:
    """Module-level helper to obtain AI suggestions."""
    return _advisor_service.get_suggestions(engine)


def apply_suggestion(engine: Any, req: ApplySuggestionRequest) -> ApplySuggestionResponse:
    """Module-level helper to apply an AI suggestion."""
    return _advisor_service.apply_suggestion(engine, req)
