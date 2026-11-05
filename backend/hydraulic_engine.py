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
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
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
# Leak-size grid for the fingerprint curves (cm²). REF_LEAK_CM2 must be in both.
# The fast solver path can afford the full grid at boot; the WNTRSimulator path
# (Net3 fallback) uses two points to keep boot time bounded.
AREA_GRID_FAST_CM2 = (5.0, 12.5, 25.0, 50.0, 100.0)
AREA_GRID_SLOW_CM2 = (25.0, 100.0)
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically via a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:10]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _atomic_save_npy(path: Path, arr: np.ndarray) -> None:
    """Save a NumPy array atomically via a temporary .npy file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:10]}.npy")
    try:
        np.save(tmp, arr)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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

    def solve(
        self, node_id: str, area_m2: float, x_init: np.ndarray | None = None
    ) -> tuple[dict[str, float], float, np.ndarray]:
        """Returns ({junction: pressure m}, leak m³/s, solution vector). Always restores area 0.

        x_init (a previous solution of a nearby leak) is only a Newton starting
        point; it changes iteration count, not the converged answer.
        """
        self.m.load_var_values_from_x(self.x0 if x_init is None else x_init)
        self._set_area(node_id, area_m2)
        try:
            self._newton()
            pressures = {j: self.m.head[j].value - self.elev[j] for j in self.junctions}
            leak = float(self.m.leak_rate[node_id].value)
            x = self.m.get_x().copy()
        finally:
            self._set_area(node_id, 0.0)
        return pressures, leak, x

    def __deepcopy__(self, memo: dict[int, Any]) -> _ReusableLeakModel:
        wn = memo.get(id(self.wn), self.wn)
        return _ReusableLeakModel(wn)


class HydraulicEngine:
    """Single-network digital twin. Thread-safe: every solve runs under one lock."""

    def __init__(
        self,
        inp_path: str = "backend/bangalore_kengeri.inp",
        meta_path: str = "backend/network_meta.json",
        num_sensors: int = NUM_SENSORS,
        cache_path: str | None = None,
        state_dir: str | Path | None = None,
        persist_active_state: bool = False,
    ) -> None:
        self.num_sensors = num_sensors
        self._lock = threading.RLock()
        self.state_dir: Path | None = Path(state_dir) if state_dir is not None else None
        self.persist_active_state: bool = bool(persist_active_state and self.state_dir is not None)
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
        self.meta: dict[str, Any] = meta

        if self.is_fallback:
            self.source_type: str = "net3_fallback"
        elif "synthetic" in str(self.meta.get("source", "")).lower() or self.meta.get("params", {}).get("source") == "synthetic":
            self.source_type = "synthetic"
        else:
            self.source_type = "osm"

        self.source_filename: Optional[str] = Path(self.inp_path).name if self.inp_path else None
        self.network_id: str = str(self.meta.get("network_name") or ("net3_fallback" if self.is_fallback else "kengeri_dma"))
        self.network_name: str = self.network_id
        self.revision_seq: int = 1
        self.network_sha256: str = self._fingerprint()[:16]
        self.network_revision: str = f"rev-{self.revision_seq}-{self.network_sha256[:8]}"
        self.updated_at_utc: str = datetime.now(timezone.utc).isoformat()
        self._baseline_snapshot: Optional[dict[str, Any]] = None
        self._previous_snapshot: Optional[dict[str, Any]] = None

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
        self.curves: np.ndarray | None = None
        self.curve_x: np.ndarray | None = None
        self._fast: _ReusableLeakModel | None = None
        self.solver_path = "wntr_simulator"
        if _ReusableLeakModel.supports(self.wn):
            self._fast = _ReusableLeakModel(self.wn)
            self.solver_path = "wntr_reusable_model"
        self.area_grid = AREA_GRID_FAST_CM2 if self._fast is not None else AREA_GRID_SLOW_CM2
        log.info("leak solver path: %s", self.solver_path)
        self.sensor_ids: list[str] = []
        self.sensor_idx: dict[str, int] = {}
        self.S: np.ndarray | None = None
        self.ref_leak_demand: np.ndarray | None = None
        self.matrix_ready = False
        self.base_pressure: dict[str, float] = {}
        self.base_flow_m3h: dict[str, float] = {}
        self.P_base: np.ndarray | None = None

    @property
    def baseline_pressure(self) -> dict[str, float]:
        return self.base_pressure

    @baseline_pressure.setter
    def baseline_pressure(self, val: dict[str, float]) -> None:
        self.base_pressure = val

    @property
    def baseline_flow_m3h(self) -> dict[str, float]:
        return self.base_flow_m3h

    @baseline_flow_m3h.setter
    def baseline_flow_m3h(self, val: dict[str, float]) -> None:
        self.base_flow_m3h = val

    @property
    def base_P(self) -> np.ndarray | None:
        return self.P_base

    @base_P.setter
    def base_P(self, val: np.ndarray | None) -> None:
        self.P_base = val

    @property
    def ref_leak_m3s(self) -> np.ndarray | None:
        return self.ref_leak_demand

    @ref_leak_m3s.setter
    def ref_leak_m3s(self, val: np.ndarray | None) -> None:
        self.ref_leak_demand = val

    @property
    def curve_S(self) -> np.ndarray | None:
        return self.curves

    @curve_S.setter
    def curve_S(self, val: np.ndarray | None) -> None:
        self.curves = val

    @property
    def using_fallback(self) -> bool:
        return self.is_fallback

    @using_fallback.setter
    def using_fallback(self, val: bool) -> None:
        self.is_fallback = val

    @property
    def latlngs(self) -> dict[str, tuple[float, float]]:
        return self.latlng

    @property
    def sha256(self) -> str:
        return self.network_sha256

    @property
    def D(self) -> np.ndarray | None:
        return self.S

    @property
    def self_ref_leak_m3s(self) -> np.ndarray | None:
        return self.ref_leak_demand

    def households_for(self, node_id: str) -> int:
        return int(self.households.get(node_id, self._derive_households(node_id) if node_id in self.junctions else 0))

    def revision_meta_dict(self) -> dict[str, Any]:
        return self.get_revision_meta()

    def promote_network(
        self,
        new_wn: wntr.network.WaterNetworkModel,
        meta: dict[str, Any] | None = None,
        source_type: str = "imported_inp",
        source_filename: str | None = None,
        network_name: str | None = None,
        sensor_ids: list[str] | None = None,
        rebuild_matrix: bool = True,
    ) -> dict[str, Any]:
        return self.activate_network(
            new_wn,
            meta=meta,
            source_type=source_type,
            source_filename=source_filename,
            network_name=network_name,
            sensor_ids=sensor_ids,
        )

    def _fingerprint(self, target_wn: wntr.network.WaterNetworkModel | None = None) -> str:
        wn = target_wn or self.wn
        h = hashlib.sha256()
        for n in sorted(wn.node_name_list):
            node = wn.get_node(n)
            coords = getattr(node, "coordinates", (0.0, 0.0))
            elev = getattr(node, "elevation", getattr(node, "base_head", 0.0))
            demand = getattr(node, "base_demand", 0.0)
            h.update(f"{n}:{coords}:{elev}:{demand};".encode())
        for l in sorted(wn.link_name_list):
            link = wn.get_link(l)
            u, v = link.start_node_name, link.end_node_name
            length = getattr(link, "length", 0.0)
            diam = getattr(link, "diameter", 0.0)
            roughness = getattr(link, "roughness", 0.0)
            status = str(link.initial_status)
            h.update(f"{l}:{u}->{v}:{length}:{diam}:{roughness}:{status};".encode())
        return h.hexdigest()

    def get_revision_meta(self) -> dict[str, Any]:
        return {
            "network_id": self.network_id,
            "network_revision": self.network_revision,
            "source_type": self.source_type,
            "source_filename": self.source_filename,
            "sha256": self.network_sha256,
            "crs": "EPSG:4326",
            "created_at_utc": self.updated_at_utc,
            "num_nodes": len(self.wn.node_name_list),
            "num_pipes": len(self.wn.pipe_name_list),
            "can_rollback": self._previous_snapshot is not None,
            "validation_summary": {
                "junctions": len(self.junctions),
                "reservoirs": len(self.wn.reservoir_name_list),
                "sensors": list(self.sensor_ids),
                "min_baseline_pressure_m": round(float(min(self.baseline_pressure.values())), 2) if self.baseline_pressure else 0.0,
                "max_baseline_pressure_m": round(float(max(self.baseline_pressure.values())), 2) if self.baseline_pressure else 0.0,
            },
        }
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
        """One steady-state WNTRSimulator run on the current model state.

        Raises instead of returning unconverged numbers (WNTR only warns by default).
        """
        res = wntr.sim.WNTRSimulator(self.wn).run_sim(convergence_error=True)
        if res.error_code is not None:
            raise RuntimeError(f"WNTRSimulator failed (error_code={res.error_code})")
        return res

    def _solve_with_leak(
        self, node_id: str, area_cm2: float, x_init: np.ndarray | None = None
    ) -> tuple[np.ndarray, float]:
        """Inject a leak, solve, ALWAYS restore. Returns (sensor pressures m, leak m³/s)."""
        p, leak, _ = self._solve_with_leak_x(node_id, area_cm2, x_init)
        return p, leak

    def _solve_with_leak_x(
        self, node_id: str, area_cm2: float, x_init: np.ndarray | None = None
    ) -> tuple[np.ndarray, float, np.ndarray | None]:
        if self._fast is not None:
            with self._lock:
                p_all, leak, x = self._fast.solve(node_id, cm2_to_m2(area_cm2), x_init)
            return np.array([p_all[s] for s in self.sensor_ids], dtype=float), leak, x
        p, leak = self._solve_with_leak_wntr(node_id, area_cm2)
        return p, leak, None

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
        self.sensor_idx = {s: i for i, s in enumerate(chosen)}
        self.min_hop_separation = min_hop_separation
        if self.base_pressure:
            self.P_base = np.array([self.base_pressure[s] for s in chosen])
        self.matrix_ready = False
        return chosen

    # ─────────────────────────── sensitivity matrix ───────────────────────────
    def _cache_key(self) -> dict[str, Any]:
        return {
            "inp_sha256": self.inp_sha256,
            "network_sha256": self.network_sha256,
            "sensor_ids": self.sensor_ids,
            "junctions": self.junctions,
            "ref_leak_cm2": REF_LEAK_CM2,
            "area_grid_cm2": list(self.area_grid),
            "wntr_version": wntr.__version__,  # warm-start vector layout is WNTR-internal
        }

    def build_or_load_sensitivity_matrix(self, use_cache: bool = True) -> np.ndarray:
        """Fingerprint curves C[j, g] = P_base − P_leak(j, area_g) at the 5 sensors.

        S = C[:, ref] is the classic N×5 sensitivity matrix for the 25 cm² reference
        leak (used for the cosine heatmap). The full curve over the area grid lets
        screening account for how the drop *shape* changes with leak size.
        Cache: .npy (curves) + .json sidecar (+ _x.npy warm starts on the fast path).
        """
        if self.P_base is None:
            self.run_baseline()
        sidecar = self.cache_path.with_suffix(".json")
        xcache = self.cache_path.with_name(self.cache_path.stem + "_x.npy")
        key = self._cache_key()
        g_ref = self.area_grid.index(REF_LEAK_CM2)
        if use_cache and self.cache_path.exists() and sidecar.exists():
            try:
                side = json.loads(sidecar.read_text(encoding="utf-8"))
                # Backward compatibility: existing caches before network_sha256 was added remain valid if inp_sha256 matches
                expected = dict(key)
                if "network_sha256" not in side:
                    expected.pop("network_sha256", None)
                if all(side.get(k) == v for k, v in expected.items()):
                    curves = np.load(self.cache_path)
                    x = np.load(xcache) if self._fast is not None else None
                    if x is not None and x.shape[-1] != self._fast.x0.shape[0]:
                        raise ValueError("warm-start vectors do not match the hydraulic model")
                    self._set_curves(curves, np.array(side["leak_demand_m3s"]), x, g_ref)
                    log.info("sensitivity matrix loaded from cache %s", self.cache_path)
                    if self._baseline_snapshot is None:
                        self._baseline_snapshot = self._take_snapshot()
                    return self.S
            except (OSError, ValueError, KeyError) as exc:
                log.warning("ignoring bad matrix cache: %s", exc)
        t0 = time.perf_counter()
        n, ng = len(self.junctions), len(self.area_grid)
        curves = np.zeros((n, ng, len(self.sensor_ids)))
        leaks = np.zeros((n, ng))
        xs: list[list[np.ndarray]] = []
        for i, j in enumerate(self.junctions):
            row_x, x_prev = [], None
            for g, area in enumerate(self.area_grid):
                p, leak, x = self._solve_with_leak_x(j, area, x_prev)
                curves[i, g] = self.P_base - p
                leaks[i, g] = leak
                row_x.append(x)
                x_prev = x
            xs.append(row_x)
        x_arr = np.array(xs) if self._fast is not None else None
        self._set_curves(curves, leaks, x_arr, g_ref)
        log.info("sensitivity curves %s built in %.1f s", curves.shape, time.perf_counter() - t0)
        if use_cache:
            try:
                _atomic_save_npy(self.cache_path, curves)
                if x_arr is not None:
                    _atomic_save_npy(xcache, x_arr)
                _atomic_write_text(sidecar, json.dumps({**key, "leak_demand_m3s": leaks.tolist()}))
            except OSError as exc:
                log.warning("could not write matrix cache: %s", exc)
        if self._baseline_snapshot is None:
            self._baseline_snapshot = self._take_snapshot()
        return self.S
    def _set_curves(self, curves: np.ndarray, leaks: np.ndarray, x: np.ndarray | None, g_ref: int) -> None:
        self.curves = curves
        self.curve_x = x
        self.S = curves[:, g_ref, :]
        self.ref_leak_demand = leaks[:, g_ref]
        self.matrix_ready = True

    def _fit_curves(self, dp_obs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Closest point to dp_obs on every junction's piecewise-linear size curve.

        The curve runs origin (no leak) → grid points; the last segment is
        extended to the 100 cm² cap. Returns (residual m, estimated area cm²) per junction.
        """
        n = self.curves.shape[0]
        pts = np.concatenate([np.zeros((n, 1, self.curves.shape[2])), self.curves], axis=1)
        areas = np.concatenate([[0.0], np.array(self.area_grid)])
        best_r = np.full(n, np.inf)
        best_a = np.full(n, REF_LEAK_CM2)
        for g in range(len(areas) - 1):
            a, b = pts[:, g, :], pts[:, g + 1, :]
            d = b - a
            dd = np.einsum("ij,ij->i", d, d)
            t = np.divide(np.einsum("ij,ij->i", dp_obs - a, d), dd, out=np.zeros(n), where=dd > 0)
            hi = (LEAK_AREA_MAX_CM2 - areas[g]) / (areas[g + 1] - areas[g]) if g == len(areas) - 2 else 1.0
            t = np.clip(t, 0.0, hi)
            r = np.linalg.norm(dp_obs - (a + t[:, None] * d), axis=1)
            area = areas[g] + t * (areas[g + 1] - areas[g])
            better = r < best_r
            best_r[better], best_a[better] = r[better], area[better]
        return best_r, np.clip(best_a, LEAK_AREA_MIN_CM2, LEAK_AREA_MAX_CM2)

    def boot(self, strategy: str = "betweenness_separated", use_cache: bool = True) -> "HydraulicEngine":
        """baseline → sensors → matrix (restoring persisted active state if enabled)."""
        self.run_baseline()
        self.select_sentinel_sensors(strategy=strategy)
        self.build_or_load_sensitivity_matrix(use_cache=use_cache)
        if self.persist_active_state:
            self._try_restore_persisted_state()
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
        # Candidate screening: size-aware fit against each junction's fingerprint
        # curve (cosine against a single 25 cm² row misranks other leak sizes).
        fit_r, fit_area = self._fit_curves(dp_obs)
        order = np.lexsort((-cos, fit_r))[:TOP_K_CANDIDATES]

        # e. stage 2: verify each candidate at its estimated size, re-rank by residual
        verified = []
        for idx in order:
            j = self.junctions[int(idx)]
            est_area = float(fit_area[idx])
            x_init = None
            if self.curve_x is not None:
                g_near = int(np.argmin(np.abs(np.array(self.area_grid) - est_area)))
                x_init = self.curve_x[idx, g_near]
            p_sim, leak_sim = self._solve_with_leak(j, est_area, x_init)
            dp_sim = self.P_base - p_sim
            best = (float(np.linalg.norm(dp_obs - dp_sim)), j, est_area, leak_sim * 60000.0,
                    _cosine(dp_obs, dp_sim))
            # One secant step on the leak size: match the drop magnitude along dp_obs,
            # using the nearest exact grid fingerprint as the second point.
            if obs_norm > 0:
                u = dp_obs / obs_norm
                grid = np.array(self.area_grid)
                g2 = int(np.argmin(np.where(np.abs(grid - est_area) > 1e-6, np.abs(grid - est_area), np.inf)))
                f1, f2 = float(dp_sim @ u), float(self.curves[idx, g2] @ u)
                if abs(f1 - f2) > 1e-9:
                    a2 = est_area + (obs_norm - f1) * (est_area - grid[g2]) / (f1 - f2)
                    a2 = float(min(max(a2, LEAK_AREA_MIN_CM2), LEAK_AREA_MAX_CM2))
                    if abs(a2 - est_area) > 1e-3:
                        p2, leak2 = self._solve_with_leak(j, a2, x_init)
                        dp2 = self.P_base - p2
                        r2 = float(np.linalg.norm(dp_obs - dp2))
                        if r2 < best[0]:
                            best = (r2, j, a2, leak2 * 60000.0, _cosine(dp_obs, dp2))
            verified.append(best)
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
            "network_revision": self.network_revision,
        }

    # ─────────────────────────── isolation ───────────────────────────
    def _graph_without(self, link_ids: set[str]) -> nx.Graph:
        """Copy of the open-link graph with the given links removed (valves closed)."""
        g = self.graph.copy()
        g.remove_edges_from([(u, v) for u, v, d in self.graph.edges(data=True) if d["link"] in link_ids])
        return g

    def _unsupplied_junctions(self, g: nx.Graph) -> list[str]:
        reachable: set[str] = set()
        for s in self.sources:
            if s in g:
                reachable |= nx.node_connected_component(g, s)
        return sorted((j for j in self.junctions if j not in reachable), key=_id_sort_key)

    def isolation_plan(self, node_id: str | None = None, leak_node_id: str | None = None) -> dict[str, Any]:
        """Close every open pipe touching node_id (a valve on every pipe is assumed)."""
        target = node_id if node_id is not None else leak_node_id
        if target is None:
            raise KeyError("node_id or leak_node_id is required")
        if target not in self.junctions:
            raise KeyError(target)
        valves = sorted({
            (d.get("link") or d.get("name"))
            for _, _, d in self.graph.edges(target, data=True)
            if (
                d.get("kind") == "pipe"
                if "kind" in d
                else self.wn.get_link(d.get("link") or d.get("name")).link_type == "Pipe"
            )
        })
        isolated = self._unsupplied_junctions(self._graph_without(set(valves)))
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
        cut_off = set(self._unsupplied_junctions(self._graph_without(set(valve_ids))))
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
            "network_revision": self.network_revision,
            "revision_meta": self.get_revision_meta(),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "network_name": self.network_name,
            "num_junctions": len(self.junctions),
            "num_pipes": len(self.wn.pipe_name_list),
            "sensor_ids": list(self.sensor_ids),
            "matrix_ready": self.matrix_ready,
            "network_revision": self.network_revision,
            "source": self.source_type,
            "can_rollback": self._previous_snapshot is not None,
        }

    def auto_presets(self) -> list[dict[str, Any]]:
        """3 demo presets on real IDs (used when data_utils/presets.json has no valid entries)."""
        sensors, src_adj = set(self.sensor_ids), set()
        for s in self.sources:
            if s in self.graph:
                src_adj |= set(self.graph.neighbors(s))
        ok = [j for j in self.junctions if j not in sensors and j not in src_adj]
        if len(ok) < 3:
            ok = list(self.junctions)
        used: set[str] = set()

        def pick(cands: list[str]) -> str:
            for c in cands:
                if c not in used:
                    used.add(c)
                    return c
            c = next((j for j in ok if j not in used), ok[0] if ok else self.junctions[0])
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

    def clone_for_scenario(self) -> tuple[wntr.network.WaterNetworkModel, dict[str, Any]]:
        """Isolated clone of the network model and scenario context."""
        with self._lock:
            self.wn.reset_initial_values()
            wn_copy = copy.deepcopy(self.wn)
            scenario_context = {
                "network_revision": self.network_revision,
                "junctions": list(self.junctions),
                "sensor_ids": list(self.sensor_ids),
                "baseline_pressure": dict(self.baseline_pressure),
                "baseline_flow_m3h": dict(self.baseline_flow_m3h),
                "households": dict(self.households),
                "road_class": dict(self.road_class),
                "latlng": dict(self.latlng),
            }
            return wn_copy, scenario_context

    def _take_snapshot(self) -> dict[str, Any]:
        return {
            "wn": copy.deepcopy(self.wn),
            "meta": copy.deepcopy(self.meta),
            "network_name": self.network_name,
            "network_id": self.network_id,
            "source_type": self.source_type,
            "source_filename": self.source_filename,
            "network_sha256": self.network_sha256,
            "revision_seq": self.revision_seq,
            "network_revision": self.network_revision,
            "updated_at_utc": self.updated_at_utc,
            "junctions": list(self.junctions),
            "sources": list(self.sources),
            "latlng": copy.deepcopy(self.latlng),
            "center": self.center,
            "households": copy.deepcopy(self.households),
            "road_class": copy.deepcopy(self.road_class),
            "graph": self.graph.copy() if self.graph is not None else None,
            "_fast": self._fast,
            "baseline_pressure": copy.deepcopy(self.base_pressure),
            "baseline_flow_m3h": copy.deepcopy(self.base_flow_m3h),
            "base_pressure": copy.deepcopy(self.base_pressure),
            "base_flow_m3h": copy.deepcopy(self.base_flow_m3h),
            "base_P": None if self.P_base is None else self.P_base.copy(),
            "P_base": None if self.P_base is None else self.P_base.copy(),
            "sensor_ids": list(self.sensor_ids),
            "sensor_idx": dict(self.sensor_idx),
            "S": None if self.S is None else self.S.copy(),
            "ref_leak_m3s": None if self.ref_leak_demand is None else self.ref_leak_demand.copy(),
            "ref_leak_demand": None if self.ref_leak_demand is None else self.ref_leak_demand.copy(),
            "curve_S": None if self.curves is None else self.curves.copy(),
            "curves": None if self.curves is None else self.curves.copy(),
            "curve_x": None if self.curve_x is None else self.curve_x.copy(),
            "area_grid": tuple(self.area_grid),
            "matrix_ready": self.matrix_ready,
            "solver_path": self.solver_path,
            "is_fallback": self.is_fallback,
        }

    def _restore_snapshot(self, snap: dict[str, Any]) -> None:
        self.wn = copy.deepcopy(snap["wn"])
        self.meta = copy.deepcopy(snap.get("meta", {}))
        self.network_name = snap["network_name"]
        self.network_id = snap.get("network_id", self.network_name)
        self.source_type = snap["source_type"]
        self.source_filename = snap["source_filename"]
        self.network_sha256 = snap["network_sha256"]
        self.revision_seq = snap["revision_seq"]
        self.network_revision = snap["network_revision"]
        self.updated_at_utc = snap["updated_at_utc"]
        self.junctions = list(snap["junctions"])
        self.sources = list(snap.get("sources", list(self.wn.reservoir_name_list) + list(self.wn.tank_name_list)))
        self.latlng = copy.deepcopy(snap["latlng"])
        self.center = snap["center"]
        self.households = copy.deepcopy(snap["households"])
        self.road_class = copy.deepcopy(snap["road_class"])
        self.graph = snap["graph"].copy() if snap.get("graph") is not None else self._build_graph()
        if snap.get("_fast") is not None and _ReusableLeakModel.supports(self.wn):
            self._fast = _ReusableLeakModel(self.wn)
            self.solver_path = "wntr_reusable_model"
        else:
            self._fast = None
            self.solver_path = "wntr_simulator"
        self.base_pressure = copy.deepcopy(snap.get("base_pressure", snap.get("baseline_pressure", {})))
        self.base_flow_m3h = copy.deepcopy(snap.get("base_flow_m3h", snap.get("baseline_flow_m3h", {})))
        p_base = snap.get("P_base") if snap.get("P_base") is not None else snap.get("base_P")
        self.P_base = p_base.copy() if p_base is not None else None
        self.sensor_ids = list(snap["sensor_ids"])
        self.sensor_idx = dict(snap.get("sensor_idx", {s: i for i, s in enumerate(self.sensor_ids)}))
        self.S = snap["S"].copy() if snap.get("S") is not None else None
        ref_leak = snap.get("ref_leak_demand") if snap.get("ref_leak_demand") is not None else snap.get("ref_leak_m3s")
        self.ref_leak_demand = ref_leak.copy() if ref_leak is not None else None
        curves = snap.get("curves") if snap.get("curves") is not None else snap.get("curve_S")
        self.curves = curves.copy() if curves is not None else None
        self.curve_x = snap["curve_x"].copy() if snap.get("curve_x") is not None else None
        self.area_grid = tuple(
            snap.get("area_grid", AREA_GRID_FAST_CM2 if self._fast is not None else AREA_GRID_SLOW_CM2)
        )
        self.matrix_ready = snap["matrix_ready"]
        self.solver_path = snap.get("solver_path", self.solver_path)
        self.is_fallback = snap.get("is_fallback", False)

    def activate_network(
        self,
        new_wn: wntr.network.WaterNetworkModel,
        meta: dict[str, Any] | None = None,
        source_type: str = "imported_inp",
        source_filename: str | None = None,
        network_name: str | None = None,
        sensor_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            prev_snap = self._take_snapshot()
            if self._baseline_snapshot is None:
                self._baseline_snapshot = prev_snap

            try:
                self._apply_invariants(new_wn)
                self.wn = new_wn
                self.meta = copy.deepcopy(meta) if meta else {}
                self.source_type = source_type
                self.source_filename = source_filename
                self.network_id = network_name or str(self.meta.get("network_name") or "imported_network")
                self.network_name = self.network_id
                self.junctions = list(self.wn.junction_name_list)
                self.sources = list(self.wn.reservoir_name_list) + list(self.wn.tank_name_list)
                self.latlng = map_network_coordinates(self.wn)
                c = self.meta.get("center")
                self.center = (c["lat"], c["lng"]) if c else network_center(self.latlng.values())
                self.households = {
                    j: int(self.meta.get("households", {}).get(j, self._derive_households(j)))
                    for j in self.junctions
                }
                self.road_class = {
                    l: self.meta.get("road_class", {}).get(l) or self._derive_road_class(l)
                    for l in self.wn.link_name_list
                }
                self.graph = self._build_graph()
                if _ReusableLeakModel.supports(self.wn):
                    self._fast = _ReusableLeakModel(self.wn)
                    self.solver_path = "wntr_reusable_model"
                else:
                    self._fast = None
                    self.solver_path = "wntr_simulator"
                self.area_grid = AREA_GRID_FAST_CM2 if self._fast is not None else AREA_GRID_SLOW_CM2
                self.run_baseline()

                if sensor_ids and len(sensor_ids) == self.num_sensors and all(s in self.junctions for s in sensor_ids):
                    self.sensor_ids = list(sensor_ids)
                    self.sensor_idx = {s: i for i, s in enumerate(self.sensor_ids)}
                    if self.base_pressure:
                        self.P_base = np.array([self.base_pressure[s] for s in self.sensor_ids])
                    self.matrix_ready = False
                else:
                    self.select_sentinel_sensors()

                self.build_or_load_sensitivity_matrix(use_cache=False)

                self.revision_seq = prev_snap["revision_seq"] + 1
                self.network_sha256 = self._fingerprint()[:16]
                self.network_revision = f"rev-{self.revision_seq}-{self.network_sha256[:8]}"
                self.updated_at_utc = datetime.now(timezone.utc).isoformat()
                self._previous_snapshot = prev_snap
                self._persist_active_state_to_disk()
            except Exception:
                self._restore_snapshot(prev_snap)
                raise

            return self.get_revision_meta()

    def rollback_network(self, target: str = "previous") -> dict[str, Any]:
        with self._lock:
            if target == "previous":
                if self._previous_snapshot is None:
                    raise ValueError("No previous network revision available for rollback")
                self._restore_snapshot(self._previous_snapshot)
                self._previous_snapshot = None
            elif target == "baseline":
                if self._baseline_snapshot is None:
                    raise ValueError("No baseline network revision available for rollback")
                self._restore_snapshot(copy.deepcopy(self._baseline_snapshot))
                self._previous_snapshot = None
            else:
                raise ValueError(f"Unknown rollback target: {target!r}")

            if self._baseline_snapshot is not None and self.network_revision == self._baseline_snapshot.get("network_revision"):
                self._clear_persisted_state_on_disk()
            else:
                self._persist_active_state_to_disk()
            return self.get_revision_meta()

    def _persist_active_state_to_disk(self) -> None:
        """Atomically persist active mutated network state to state_dir when enabled."""
        if not self.persist_active_state or self.state_dir is None:
            return
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            inp_file = self.state_dir / "active_network.inp"
            state_file = self.state_dir / "active_state.json"
            tmp_inp = self.state_dir / f".active_network.{uuid.uuid4().hex[:8]}.inp"
            try:
                wntr.network.write_inpfile(self.wn, str(tmp_inp))
                os.replace(tmp_inp, inp_file)
            finally:
                if tmp_inp.exists():
                    tmp_inp.unlink(missing_ok=True)

            state_payload = {
                "network_id": self.network_id,
                "network_revision": self.network_revision,
                "revision_seq": self.revision_seq,
                "network_sha256": self.network_sha256,
                "source_type": self.source_type,
                "source_filename": self.source_filename,
                "updated_at_utc": self.updated_at_utc,
                "sensor_ids": list(self.sensor_ids),
                "meta": self.meta,
            }
            _atomic_write_text(state_file, json.dumps(state_payload))
        except Exception as exc:
            log.warning("Could not persist active network state to %s: %s", self.state_dir, exc)

    def _clear_persisted_state_on_disk(self) -> None:
        """Remove persisted active state when rolled back to baseline."""
        if not self.persist_active_state or self.state_dir is None:
            return
        for fname in ("active_state.json", "active_network.inp"):
            p = self.state_dir / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    def _try_restore_persisted_state(self) -> None:
        """Restore persisted active network state after restart if present and valid."""
        if not self.persist_active_state or self.state_dir is None:
            return
        inp_file = self.state_dir / "active_network.inp"
        state_file = self.state_dir / "active_state.json"
        if not (inp_file.exists() and state_file.exists()):
            return
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            restored_wn = wntr.network.WaterNetworkModel(str(inp_file))
            self.activate_network(
                restored_wn,
                meta=state.get("meta") or {},
                source_type=str(state.get("source_type") or "imported_inp"),
                source_filename=state.get("source_filename"),
                network_name=str(state.get("network_id") or "restored_network"),
                sensor_ids=state.get("sensor_ids"),
            )
            if isinstance(state.get("network_sha256"), str) and state["network_sha256"]:
                self.network_sha256 = str(state["network_sha256"])
            if isinstance(state.get("revision_seq"), int) and state["revision_seq"] >= 1:
                self.revision_seq = int(state["revision_seq"])
            if isinstance(state.get("network_revision"), str) and state["network_revision"]:
                self.network_revision = str(state["network_revision"])
            else:
                self.network_revision = f"rev-{self.revision_seq}-{self.network_sha256[:8]}"
        except Exception as exc:
            log.warning("Ignoring corrupt or incompatible persisted network state in %s: %s", self.state_dir, exc)
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
