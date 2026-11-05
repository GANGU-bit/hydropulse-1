"""HydroPulse hydraulic digital twin.

Physics in brief
- Steady-state, pressure-driven demand (PDD): a junction delivers its full
  demand at ≥ 15 m and a reduced demand ∝ ((p - 2) / 13) ** 0.5 below that.
- A burst is a Torricelli orifice at a junction: Q = Cd · A · sqrt(2 g h),
  Cd = 0.75, A in m² (API takes cm², we divide by 10 000).
- Only WNTRSimulator models leaks (EpanetSimulator silently ignores them).

Detection
- 5 sentinel junctions (betweenness centrality with hop separation).
- Sensitivity matrix S (N×5): pressure drop at the sensors for a 25 cm²
  reference leak at every junction.
- Stage 1: cosine similarity between the observed drop vector and every row
  of S (shape match, roughly scale-free).
- Stage 2: re-simulate the top-5 candidates at their estimated leak size and
  re-rank by residual ‖Δp_obs − Δp_sim‖₂ (handles orifice non-linearity).
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import logging
import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import wntr
from wntr.sim import hydraulics as _wntr_hydraulics
from wntr.sim.models import param as _wntr_param
from wntr.sim.solvers import NewtonSolver, SolverStatus

from data_utils.geo_mapper import haversine_m, map_network_coordinates, network_center

log = logging.getLogger("hydropulse.engine")
logging.getLogger("wntr").setLevel(logging.WARNING)  # per-solve INFO lines are noise

# SOURCE_OF_TRUTH §6 constants
NUM_SENSORS = 5
MIN_HOP_SEPARATION = 3
TOP_K_CANDIDATES = 5
HEATMAP_EXPONENT = 8
REF_LEAK_CM2 = 25.0
LEAK_AREA_MIN_CM2, LEAK_AREA_MAX_CM2 = 0.5, 100.0
NOISE_MIN_PCT, NOISE_MAX_PCT = 0.0, 10.0
DISCHARGE_COEFF = 0.75
REQUIRED_PRESSURE_M = 15.0
MINIMUM_PRESSURE_M = 2.0
PRESSURE_EXPONENT = 0.5
LPCD = 135.0
PERSONS_PER_HOUSEHOLD = 4.5
TANKER_LITRES = 6000.0
DEFAULT_CENTER = (12.9081, 77.4852)


def cm2_to_m2(area_cm2: float) -> float:
    """Leak area: API cm² → WNTR m²."""
    return area_cm2 / 10000.0


def tankers_per_day(lpm: float) -> float:
    """L/min → 6000 L tankers per day (SOURCE_OF_TRUTH §6)."""
    return round(lpm * 1440.0 / TANKER_LITRES, 1)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _net3_path() -> str:
    hits = glob.glob(os.path.join(os.path.dirname(wntr.__file__), "**", "Net3.inp"), recursive=True)
    if not hits:
        raise FileNotFoundError("WNTR's bundled Net3.inp not found")
    return hits[0]


class _ReusableLeakModel:
    """Fast steady-state leak solves on WNTRSimulator's own hydraulic model.

    WNTRSimulator.run_sim() spends ~95% of its time rebuilding the symbolic
    model. Here every junction carries a leak term of area 0 (Q = Cd·0·√(2gh) = 0),
    the model is built once, and a solve only changes the target's leak area
    and runs WNTR's NewtonSolver from the baseline state. Same equations as
    WNTRSimulator (tests assert agreement to < 1e-4 m). Only valid for networks
    without pumps, valves, tanks or controls; otherwise the engine uses
    WNTRSimulator directly.
    """

    def __init__(self, wn: wntr.network.WaterNetworkModel) -> None:
        self.wn = copy.deepcopy(wn)
        self.junctions = list(self.wn.junction_name_list)
        for j in self.junctions:
            node = self.wn.get_node(j)
            node._leak, node._leak_status = True, True
            node._leak_area, node._leak_discharge_coeff = 0.0, DISCHARGE_COEFF
        self.m, self.updater = _wntr_hydraulics.create_hydraulic_model(self.wn)
        _wntr_param.source_head_param(self.m, self.wn)
        _wntr_param.expected_demand_param(self.m, self.wn)
        self.m.set_structure()
        self._newton()
        self.x0 = self.m.get_x().copy()
        self.elev = {j: self.wn.get_node(j).elevation for j in self.junctions}

    @staticmethod
    def supports(wn: wntr.network.WaterNetworkModel) -> bool:
        return (wn.num_pumps == 0 and wn.num_valves == 0 and wn.num_tanks == 0
                and len(list(wn.control_name_list)) == 0)

    def _newton(self) -> None:
        status, msg, _ = NewtonSolver({}).solve(self.m)
        if status != SolverStatus.converged:
            raise RuntimeError(f"solver did not converge: {msg}")

    def _set_area(self, node_id: str, area_m2: float) -> None:
        self.wn.get_node(node_id)._leak_area = area_m2
        _wntr_param.leak_area_param.build(self.m, self.wn, self.updater, index_over=[node_id])

    def solve(self, node_id: str, area_m2: float) -> tuple[dict[str, float], float]:
        """Returns ({junction: pressure m}, leak m³/s). Always restores area 0."""
        self.m.load_var_values_from_x(self.x0)
        self._set_area(node_id, area_m2)
        try:
            self._newton()
            pressures = {j: self.m.head[j].value - self.elev[j] for j in self.junctions}
            leak = float(self.m.leak_rate[node_id].value)
        finally:
            self._set_area(node_id, 0.0)
        return pressures, leak


class HydraulicEngine:
    """Single-network digital twin. Thread-safe: every solve runs under one lock."""

    def __init__(
        self,
        inp_path: str = "backend/bangalore_kengeri.inp",
        meta_path: str = "backend/network_meta.json",
        num_sensors: int = NUM_SENSORS,
        cache_path: str | None = None,
    ) -> None:
        self.num_sensors = num_sensors
        self._lock = threading.RLock()
        self.is_fallback = not Path(inp_path).exists()
        if self.is_fallback:
            log.warning("!!! %s not found — FALLING BACK to WNTR's bundled Net3 network !!!", inp_path)
            inp_path = _net3_path()
        self.inp_path = str(inp_path)
        self.inp_sha256 = hashlib.sha256(Path(self.inp_path).read_bytes()).hexdigest()
        default_cache = (
            "backend/sensitivity_matrix_net3.npy" if self.is_fallback else "backend/sensitivity_matrix.npy"
        )
        self.cache_path = Path(cache_path or default_cache)

        self.wn = wntr.network.WaterNetworkModel(self.inp_path)
        self._apply_invariants(self.wn)
        self.junctions: list[str] = list(self.wn.junction_name_list)
        self.sources: list[str] = list(self.wn.reservoir_name_list) + list(self.wn.tank_name_list)

        meta: dict[str, Any] = {}
        if not self.is_fallback and Path(meta_path).exists():
            meta = json.loads(Path(meta_path).read_text())
        self.network_name: str = meta.get("network_name") or ("net3_fallback" if self.is_fallback else Path(inp_path).stem)
        self.households: dict[str, int] = {
            j: int(meta.get("households", {}).get(j, self._derive_households(j))) for j in self.junctions
        }
        self.road_class: dict[str, str] = {
            l: meta.get("road_class", {}).get(l) or self._derive_road_class(l) for l in self.wn.link_name_list
        }
        self.latlng: dict[str, tuple[float, float]] = map_network_coordinates(self.wn)
        c = meta.get("center")
        self.center = (c["lat"], c["lng"]) if c else network_center(self.latlng.values())

        self.graph = self._build_graph()
        self._fast: _ReusableLeakModel | None = None
        self.solver_path = "wntr_simulator"
        if _ReusableLeakModel.supports(self.wn):
            self._fast = _ReusableLeakModel(self.wn)
            self.solver_path = "wntr_reusable_model"
        log.info("leak solver path: %s", self.solver_path)
        self.sensor_ids: list[str] = []
        self.S: np.ndarray | None = None
        self.ref_leak_demand: np.ndarray | None = None
        self.matrix_ready = False
        self.base_pressure: dict[str, float] = {}
        self.base_flow_m3h: dict[str, float] = {}
        self.P_base: np.ndarray | None = None

    # ─────────────────────────── setup helpers ───────────────────────────
    @staticmethod
    def _apply_invariants(wn: wntr.network.WaterNetworkModel) -> None:
        """Steady state + PDD (SOURCE_OF_TRUTH §6). Never run multi-hour sims."""
        wn.options.time.duration = 0
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
        wn.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
        wn.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

    def _derive_households(self, j: str) -> int:
        base_lpd = self.wn.get_node(j).base_demand * 1000.0 * 86400.0
        return int(min(max(round(base_lpd / (LPCD * PERSONS_PER_HOUSEHOLD)), 1), 500))

    def _derive_road_class(self, link_name: str) -> str:
        link = self.wn.get_link(link_name)
        ends = {link.start_node_name, link.end_node_name}
        if link.link_type != "Pipe" or ends & set(self.sources):
            return "feeder"
        d = link.diameter
        if d >= 0.40 - 1e-9:
            return "primary"
        if d >= 0.20:
            return "secondary"
        return "residential"

    def _build_graph(self) -> nx.Graph:
        """Undirected graph of all open links. Edge attr `link` = link id, `weight` = length (m)."""
        g = nx.Graph()
        g.add_nodes_from(self.wn.node_name_list)
        for name in self.wn.link_name_list:
            link = self.wn.get_link(name)
            if link.initial_status == wntr.network.LinkStatus.Closed:
                continue
            w = float(getattr(link, "length", 1.0) or 1.0)
            g.add_edge(link.start_node_name, link.end_node_name, link=name, weight=w)
        return g

    # ─────────────────────────── solving ───────────────────────────
    def _solve(self):
        """One steady-state WNTRSimulator run on the current model state."""
        return wntr.sim.WNTRSimulator(self.wn).run_sim()

    def _solve_with_leak(self, node_id: str, area_cm2: float) -> tuple[np.ndarray, float]:
        """Inject a leak, solve, ALWAYS restore. Returns (sensor pressures m, leak m³/s)."""
        if self._fast is not None:
            with self._lock:
                p_all, leak = self._fast.solve(node_id, cm2_to_m2(area_cm2))
            return np.array([p_all[s] for s in self.sensor_ids], dtype=float), leak
        return self._solve_with_leak_wntr(node_id, area_cm2)

    def _solve_with_leak_wntr(self, node_id: str, area_cm2: float) -> tuple[np.ndarray, float]:
        """Reference path: add_leak → WNTRSimulator → remove_leak + reset."""
        with self._lock:
            node = self.wn.get_node(node_id)
            node.add_leak(self.wn, area=cm2_to_m2(area_cm2), discharge_coeff=DISCHARGE_COEFF, start_time=0)
            try:
                res = self._solve()
            finally:
                node.remove_leak(self.wn)
                self.wn.reset_initial_values()
        p = res.node["pressure"].iloc[0]
        leak = float(res.node["leak_demand"].iloc[0][node_id])
        return p[self.sensor_ids].to_numpy(dtype=float), leak

    def run_baseline(self) -> None:
        """Solve with no leak; cache junction pressures, link flows and sensor baseline."""
        with self._lock:
            res = self._solve()
            self.wn.reset_initial_values()
        p = res.node["pressure"].iloc[0]
        q = res.link["flowrate"].iloc[0]
        self.base_pressure = {n: float(p[n]) for n in self.wn.node_name_list}
        self.base_flow_m3h = {l: float(q[l]) * 3600.0 for l in self.wn.link_name_list}
        if self.sensor_ids:
            self.P_base = np.array([self.base_pressure[s] for s in self.sensor_ids])

    # ─────────────────────────── sensors ───────────────────────────
    def select_sentinel_sensors(self, min_hop_separation: int = MIN_HOP_SEPARATION, strategy: str = "betweenness_separated") -> list[str]:
        """Pick `num_sensors` junctions.

        betweenness_separated: walk junctions by descending length-weighted
        betweenness; accept one only if it is ≥ min_hop_separation hops from
        every sensor already chosen (relax by 1 until enough are found).
        betweenness_top: plain top-N betweenness (baseline for the benchmark).
        """
        bc = nx.betweenness_centrality(self.graph, weight="weight")
        ranked = sorted(self.junctions, key=lambda j: (-bc.get(j, 0.0), j))
        if strategy == "betweenness_top":
            chosen = ranked[: self.num_sensors]
        elif strategy == "betweenness_separated":
            sep = min_hop_separation
            chosen = []
            while sep >= 0:
                chosen = []
                hops: dict[str, dict[str, int]] = {}
                for j in ranked:
                    if all(hops[s].get(j, 10**9) >= sep for s in chosen):
                        chosen.append(j)
                        hops[j] = nx.single_source_shortest_path_length(self.graph, j)
                        if len(chosen) == self.num_sensors:
                            break
                if len(chosen) == self.num_sensors:
                    break
                sep -= 1
        else:
            raise ValueError(f"unknown sensor strategy {strategy!r}")
        self.sensor_ids = chosen
        self.min_hop_separation = min_hop_separation
        if self.base_pressure:
            self.P_base = np.array([self.base_pressure[s] for s in chosen])
        self.matrix_ready = False
        return chosen

    # ─────────────────────────── sensitivity matrix ───────────────────────────
    def _cache_key(self) -> dict[str, Any]:
        return {
            "inp_sha256": self.inp_sha256,
            "sensor_ids": self.sensor_ids,
            "junctions": self.junctions,
            "ref_leak_cm2": REF_LEAK_CM2,
        }

    def build_or_load_sensitivity_matrix(self, use_cache: bool = True) -> np.ndarray:
        """S[j] = P_base − P_leak(j) at the 5 sensors for a 25 cm² leak at junction j."""
        if self.P_base is None:
            self.run_baseline()
        sidecar = self.cache_path.with_suffix(".json")
        key = self._cache_key()
        if use_cache and self.cache_path.exists() and sidecar.exists():
            try:
                side = json.loads(sidecar.read_text())
                if all(side.get(k) == v for k, v in key.items()):
                    self.S = np.load(self.cache_path)
                    self.ref_leak_demand = np.array(side["ref_leak_demand_m3s"])
                    self.matrix_ready = True
                    log.info("sensitivity matrix loaded from cache %s", self.cache_path)
                    return self.S
            except (OSError, ValueError, KeyError) as exc:
                log.warning("ignoring bad matrix cache: %s", exc)
        t0 = time.perf_counter()
        rows, leaks = [], []
        for j in self.junctions:
            p, leak = self._solve_with_leak(j, REF_LEAK_CM2)
            rows.append(self.P_base - p)
            leaks.append(leak)
        self.S = np.vstack(rows)
        self.ref_leak_demand = np.array(leaks)
        self.matrix_ready = True
        log.info("sensitivity matrix %s built in %.1f s", self.S.shape, time.perf_counter() - t0)
        if use_cache:
            try:
                np.save(self.cache_path, self.S)
                sidecar.write_text(json.dumps({**key, "ref_leak_demand_m3s": leaks}))
            except OSError as exc:
                log.warning("could not write matrix cache: %s", exc)
        return self.S

    def boot(self, strategy: str = "betweenness_separated", use_cache: bool = True) -> "HydraulicEngine":
        """baseline → sensors → matrix."""
        self.run_baseline()
        self.select_sentinel_sensors(strategy=strategy)
        self.build_or_load_sensitivity_matrix(use_cache=use_cache)
        return self

    # ─────────────────────────── detection ───────────────────────────
    def _validate(self, node_id: str, area_cm2: float, noise_pct: float = 0.0) -> None:
        if node_id not in self.junctions:
            raise KeyError(node_id)
        if not LEAK_AREA_MIN_CM2 <= area_cm2 <= LEAK_AREA_MAX_CM2:
            raise ValueError(f"leak_area_cm2 must be in [{LEAK_AREA_MIN_CM2}, {LEAK_AREA_MAX_CM2}]")
        if not NOISE_MIN_PCT <= noise_pct <= NOISE_MAX_PCT:
            raise ValueError(f"noise_pct must be in [{NOISE_MIN_PCT}, {NOISE_MAX_PCT}]")

    def simulate_and_triangulate(
        self, target_node_id: str, leak_area_cm2: float, noise_pct: float, seed: int | None = None
    ) -> dict[str, Any]:
        """Inject a leak, read the 5 sensors (+noise), localise it, plan isolation."""
        t0 = time.perf_counter()
        self._validate(target_node_id, leak_area_cm2, noise_pct)
        if not self.matrix_ready or self.S is None:
            raise RuntimeError("sensitivity matrix not ready")
        assert self.P_base is not None

        # b. inject + observe
        p_clean, leak_m3s = self._solve_with_leak(target_node_id, leak_area_cm2)
        actual_loss_lpm = leak_m3s * 60000.0

        # c. sensor noise, proportional to the mean true drop
        dp_true = self.P_base - p_clean
        sigma = (noise_pct / 100.0) * max(float(np.mean(np.abs(dp_true))), 0.05)
        rng = np.random.default_rng(seed)
        p_obs = p_clean + rng.normal(0.0, sigma, size=len(p_clean)) if sigma > 0 else p_clean
        dp_obs = self.P_base - p_obs

        # d. stage 1: cosine screening against every junction fingerprint
        norms = np.linalg.norm(self.S, axis=1)
        obs_norm = float(np.linalg.norm(dp_obs))
        denom = norms * obs_norm
        cos = np.divide(self.S @ dp_obs, denom, out=np.zeros(len(self.junctions)), where=denom > 0)
        heat = np.clip(cos, 0.0, 1.0) ** HEATMAP_EXPONENT
        if heat.max() > 0:
            heat = heat / heat.max()
        order = np.argsort(-cos, kind="stable")[:TOP_K_CANDIDATES]

        # e. stage 2: verify each candidate at its estimated size, re-rank by residual
        verified = []
        for idx in order:
            j = self.junctions[int(idx)]
            s_j = self.S[idx]
            ss = float(s_j @ s_j)
            alpha = float(dp_obs @ s_j) / ss if ss > 0 else 0.0
            est_area = min(max(REF_LEAK_CM2 * alpha, LEAK_AREA_MIN_CM2), LEAK_AREA_MAX_CM2)
            p_sim, leak_sim = self._solve_with_leak(j, est_area)
            dp_sim = self.P_base - p_sim
            resid = float(np.linalg.norm(dp_obs - dp_sim))
            verified.append((resid, j, est_area, leak_sim * 60000.0, _cosine(dp_obs, dp_sim)))
        verified.sort(key=lambda v: (v[0], v[1]))
        candidates = [
            {
                "node_id": j,
                "confidence_pct": round(min(max(100.0 * c, 0.0), 100.0), 1),
                "estimated_loss_lpm": round(lpm, 2),
                "tankers_lost_per_day": tankers_per_day(lpm),
                "estimated_leak_area_cm2": round(area, 2),
            }
            for _, j, area, lpm, c in verified
        ]
        top = candidates[0]["node_id"]
        heatmap = {j: round(float(h), 2) for j, h in zip(self.junctions, heat)}
        if max(heatmap.values(), default=0.0) <= 0.0:
            heatmap[top] = 1.0

        iso = self.isolation_plan(top)
        la, lo = self.latlng[target_node_id]
        ta, to = self.latlng[top]
        return {
            "injected_node_id": target_node_id,
            "injected_leak_area_cm2": round(float(leak_area_cm2), 2),
            "actual_loss_lpm": round(actual_loss_lpm, 2),
            "predicted_top_candidates": candidates,
            "sensor_readings": [
                {
                    "sensor_id": s,
                    "baseline_m": round(float(b), 2),
                    "observed_m": round(float(o), 2),
                    "drop_m": round(float(b - o), 2),
                }
                for s, b, o in zip(self.sensor_ids, self.P_base, p_obs)
            ],
            "node_heatmap": heatmap,
            **iso,
            "localization_error_m": round(haversine_m(la, lo, ta, to), 2),
            "compute_time_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }

    # ─────────────────────────── isolation ───────────────────────────
    def _unsupplied_junctions(self, g: nx.Graph) -> list[str]:
        reachable: set[str] = set()
        for s in self.sources:
            if s in g:
                reachable |= nx.node_connected_component(g, s)
        return sorted((j for j in self.junctions if j not in reachable), key=_id_sort_key)

    def isolation_plan(self, node_id: str) -> dict[str, Any]:
        """Close every open pipe touching node_id (a valve on every pipe is assumed)."""
        if node_id not in self.junctions:
            raise KeyError(node_id)
        valves = sorted(
            (d["link"] for _, _, d in self.graph.edges(node_id, data=True)
             if self.wn.get_link(d["link"]).link_type == "Pipe"),
            key=_id_sort_key,
        )
        g = self.graph.copy()
        g.remove_edges_from([(u, v) for u, v, d in self.graph.edges(node_id, data=True) if d["link"] in valves])
        isolated = self._unsupplied_junctions(g)
        return {
            "recommended_valves_to_close": valves,
            "isolated_node_ids": isolated,
            "affected_households_after_isolation": int(sum(self.households[j] for j in isolated)),
        }

    def simulate_isolation(self, valve_ids: list[str], target_node_id: str, leak_area_cm2: float) -> dict[str, Any]:
        """(stretch) Close valve_ids with the leak still present; report pressures and outages."""
        self._validate(target_node_id, leak_area_cm2)
        for v in valve_ids:
            if v not in self.wn.link_name_list:
                raise LookupError(v)
        g = self.graph.copy()
        g.remove_edges_from([(u, w) for u, w, d in self.graph.edges(data=True) if d["link"] in set(valve_ids)])
        cut_off = set(self._unsupplied_junctions(g))
        with self._lock:
            saved = {v: self.wn.get_link(v).initial_status for v in valve_ids}
            node = self.wn.get_node(target_node_id)
            try:
                for v in valve_ids:
                    self.wn.get_link(v).initial_status = wntr.network.LinkStatus.Closed
                node.add_leak(self.wn, area=cm2_to_m2(leak_area_cm2), discharge_coeff=DISCHARGE_COEFF, start_time=0)
                res = self._solve()
            finally:
                node.remove_leak(self.wn)
                for v, st in saved.items():
                    self.wn.get_link(v).initial_status = st
                self.wn.reset_initial_values()
        p = res.node["pressure"].iloc[0]
        pressures = {j: (0.0 if j in cut_off else round(float(p[j]), 2)) for j in self.junctions}
        supplied = [v for j, v in pressures.items() if j not in cut_off]
        no_supply = sorted(
            {j for j in self.junctions if j in cut_off or pressures[j] <= MINIMUM_PRESSURE_M}, key=_id_sort_key
        )
        return {
            "node_pressures": pressures,
            "min_pressure_m": round(min(supplied), 2) if supplied else 0.0,
            "nodes_without_supply": no_supply,
        }

    # ─────────────────────────── API payloads ───────────────────────────
    def _node_type(self, n: str) -> str:
        if n in self.wn.reservoir_name_list:
            return "reservoir"
        if n in self.wn.tank_name_list:
            return "tank"
        return "junction"

    def get_network_payload(self) -> dict[str, Any]:
        """GET /api/network body (contract v1.0)."""
        sensors = set(self.sensor_ids)
        nodes = []
        for n in self.wn.node_name_list:
            node = self.wn.get_node(n)
            t = self._node_type(n)
            lat, lng = self.latlng[n]
            elev = getattr(node, "elevation", None)
            if elev is None:
                elev = getattr(node, "base_head", 0.0)
            is_j = t == "junction"
            nodes.append({
                "id": n,
                "type": t,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "elevation_m": round(float(elev), 2),
                "base_pressure_m": round(self.base_pressure.get(n, 0.0), 2) if is_j else 0.0,
                "base_demand_lps": round(node.base_demand * 1000.0, 2) if is_j else 0.0,
                "households": self.households[n] if is_j else 0,
                "is_sensor": n in sensors,
                "leak_probability": 0.0,
            })
        pipes = []
        for l in self.wn.link_name_list:
            link = self.wn.get_link(l)
            closed = link.initial_status == wntr.network.LinkStatus.Closed
            pipes.append({
                "id": l,
                "source": link.start_node_name,
                "target": link.end_node_name,
                "diameter_mm": round(float(getattr(link, "diameter", 0.0) or 0.0) * 1000.0, 2),
                "length_m": round(float(getattr(link, "length", 0.0) or 0.0), 2),
                "flow_m3h": round(self.base_flow_m3h.get(l, 0.0), 2),
                "status": "CLOSED" if closed else "OPEN",
                "road_class": self.road_class[l],
            })
        return {
            "network_name": self.network_name,
            "center": {"lat": round(self.center[0], 6), "lng": round(self.center[1], 6)},
            "sensor_ids": list(self.sensor_ids),
            "nodes": nodes,
            "pipes": pipes,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "network_name": self.network_name,
            "num_junctions": len(self.junctions),
            "num_pipes": len(self.wn.pipe_name_list),
            "sensor_ids": list(self.sensor_ids),
            "matrix_ready": self.matrix_ready,
        }

    def auto_presets(self) -> list[dict[str, Any]]:
        """3 demo presets on real IDs (used when data_utils/presets.json has no valid entries)."""
        sensors, src_adj = set(self.sensor_ids), set()
        for s in self.sources:
            if s in self.graph:
                src_adj |= set(self.graph.neighbors(s))
        ok = [j for j in self.junctions if j not in sensors and j not in src_adj]
        used: set[str] = set()

        def pick(cands: list[str]) -> str:
            for c in cands:
                if c not in used:
                    used.add(c)
                    return c
            c = next(j for j in ok if j not in used)
            used.add(c)
            return c

        def max_diam(j: str) -> float:
            return max((float(getattr(self.wn.get_link(d["link"]), "diameter", 0) or 0)
                        for _, _, d in self.graph.edges(j, data=True)), default=0.0)

        mainline = pick(sorted(ok, key=lambda j: (-max_diam(j), _id_sort_key(j))))
        dead_ends = [j for j in ok if self.graph.degree(j) == 1]
        if self.sources:
            dist = nx.single_source_dijkstra_path_length(self.graph, self.sources[0], weight="weight")
            dead_ends.sort(key=lambda j: -dist.get(j, 0.0))
        seep = pick(dead_ends or sorted(ok, key=lambda j: (self.graph.degree(j), _id_sort_key(j))))
        bc = nx.betweenness_centrality(self.graph, weight="weight")
        by_bc = sorted(ok, key=lambda j: (bc.get(j, 0.0), _id_sort_key(j)))
        med = statistics.median_low(range(len(by_bc))) if by_bc else 0
        crack = pick(by_bc[med:] + by_bc[:med])
        return [
            {"id": "kengeri_mainline_rupture", "label": "Kengeri Mainline Rupture",
             "description": "Large burst on a junction fed by the widest main.",
             "target_node_id": mainline, "leak_area_cm2": 45.0, "noise_pct": 1.5},
            {"id": "residential_lane_seep", "label": "Residential Lane Seep",
             "description": "Small seep at a dead-end residential lane far from the source.",
             "target_node_id": seep, "leak_area_cm2": 8.0, "noise_pct": 3.0},
            {"id": "joint_crack_junction", "label": "Joint Crack under Junction",
             "description": "Medium crack at a typical (median-centrality) junction.",
             "target_node_id": crack, "leak_area_cm2": 20.0, "noise_pct": 2.0},
        ]

    def clone_with_sensors(self, strategy: str) -> "HydraulicEngine":
        """Independent engine copy with a different sensor strategy (benchmark use)."""
        other = copy.copy(self)
        other.wn = copy.deepcopy(self.wn)
        other._lock = threading.RLock()
        other._fast = _ReusableLeakModel(self.wn) if self._fast is not None else None
        other.select_sentinel_sensors(strategy=strategy)
        other.build_or_load_sensitivity_matrix(use_cache=False)
        return other


def _id_sort_key(s: str) -> tuple:
    """Natural sort for ids like J2 < J10 and Net3's '10' < '101'. IDs stay strings."""
    head = s.rstrip("0123456789")
    tail = s[len(head):]
    return (head, int(tail) if tail else -1, s)
