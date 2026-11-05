"""HydroPulse IoT Telemetry, Pipe Condition, and Safe Auto-Valve Manager.

Contract v1.1 implementation adhering to:
- Thread-safe bounded rolling telemetry buffer (maxlen=250) and sequence deduplication.
- Pure simulated stream generation without runaway background threads.
- Physical RUL data transparency: clear distinction between certified structural RUL
  and illustrative hydraulic load index.
- Safe human-in-the-loop auto-valve isolation simulation with two-phase commit and
  explicit safety refusal gates.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import HTTPException

try:
    from backend.hydraulic_engine import MINIMUM_PRESSURE_M
except ImportError:
    MINIMUM_PRESSURE_M = 2.0

from data_utils.schemas import (
    AutoValveRequest,
    AutoValveResponse,
    IoTStateResponse,
    PipeConditionItem,
    PipeConditionResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TelemetryPacket,
)

log = logging.getLogger("hydropulse.iot")


def _parse_iso8601_utc(ts_str: str) -> datetime:
    """Parse ISO-8601 string and return a timezone-aware UTC datetime."""
    ts_clean = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts_clean)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class IoTManager:
    """Manages IoT telemetry ingestion, state buffer, pipe health indices, and simulated valve operations."""

    def __init__(self, seed: int = 42) -> None:
        self._lock = threading.RLock()
        self.buffer: collections.deque[TelemetryPacket] = collections.deque(maxlen=250)
        self.latest_readings: dict[str, TelemetryPacket] = {}
        self.seen_sequences: dict[str, int] = {}
        self.latest_source_timestamps: dict[str, datetime] = {}
        self._generated_sequences: dict[str, int] = {}

        self.stream_enabled: bool = False
        self.seed: int = seed
        self.auto_valve_enabled: bool = False  # MUST default to False
        self.emergency_stop: bool = False
        self.active_valve_closures: list[str] = []
        self.pending_valve_proposal: dict[str, Any] | None = None
        self._bound_revision: str | None = None

    def _sync_revision(self, engine: Any) -> None:
        """Track engine revision and invalidate stale operational state when the network changes."""
        current_rev = str(getattr(engine, "network_revision", ""))
        if not current_rev:
            return

        if self._bound_revision is None:
            self._bound_revision = current_rev
            return

        if self._bound_revision != current_rev:
            log.info(
                "Network revision transition: '%s' -> '%s'. Clearing active closures and proposals.",
                self._bound_revision,
                current_rev,
            )
            self._bound_revision = current_rev
            self.active_valve_closures = []
            self.pending_valve_proposal = None
            # Mark prior latest readings as STALE across revision boundary
            for target_id, pkt in list(self.latest_readings.items()):
                if pkt.network_revision != current_rev and pkt.quality != "STALE":
                    self.latest_readings[target_id] = pkt.model_copy(update={"quality": "STALE"})

    def _next_sim_seq(self, source_id: str) -> int:
        """Generate strictly increasing sequence IDs for deterministic simulated sources."""
        curr = max(self.seen_sequences.get(source_id, 0), self._generated_sequences.get(source_id, 0))
        next_seq = curr + 1
        self._generated_sequences[source_id] = next_seq
        return next_seq

    # ─────────────────────────── Telemetry State & Ingestion ───────────────────────────

    def get_state(
        self, engine: Any, generate_demo_tick: bool = False, seed: int | None = None
    ) -> IoTStateResponse:
        """Return the current IoT telemetry and auto-valve status."""
        with self._lock:
            self._sync_revision(engine)

            if generate_demo_tick:
                tick_seed = seed if seed is not None else self.seed
                packets = self.generate_simulated_packets(engine, seed=tick_seed)
                self.ingest(engine, TelemetryIngestRequest(packets=packets))

            return IoTStateResponse(
                network_revision=getattr(engine, "network_revision", "rev-unknown"),
                stream_enabled=self.stream_enabled,
                stream_mode="SIMULATED" if self.stream_enabled else "OFF",
                seed=self.seed,
                buffer_size=len(self.buffer),
                latest_readings=dict(self.latest_readings),
                auto_valve_enabled=self.auto_valve_enabled,
                emergency_stop=self.emergency_stop,
                active_valve_closures=list(self.active_valve_closures),
                pending_valve_proposal=self.pending_valve_proposal,
            )

    def ingest(self, engine: Any, req: TelemetryIngestRequest) -> TelemetryIngestResponse:
        """Validate and ingest a batch of telemetry packets into the rolling buffer."""
        with self._lock:
            self._sync_revision(engine)
            active_rev = getattr(engine, "network_revision", "")

            accepted = 0
            rejected = 0
            deduplicated = 0
            errors: list[str] = []

            # Pre-compute valid target IDs
            junctions = set(getattr(engine, "junctions", []))
            wn = getattr(engine, "wn", None)
            all_nodes = junctions | set(getattr(wn, "node_name_list", []))
            all_links = set(getattr(wn, "link_name_list", []))

            for pkt in req.packets:
                # 1. Network revision check
                if pkt.network_revision != active_rev:
                    rejected += 1
                    errors.append(
                        f"Stale revision: packet network_revision '{pkt.network_revision}' does not match active '{active_rev}'"
                    )
                    continue

                # 2. Target ID verification
                if pkt.measurement_type in ("pressure_m", "demand_lps"):
                    if pkt.target_id not in all_nodes:
                        rejected += 1
                        errors.append(
                            f"Target node '{pkt.target_id}' not found in network nodes for measurement '{pkt.measurement_type}'"
                        )
                        continue
                elif pkt.measurement_type == "flow_m3h":
                    if pkt.target_id not in all_links:
                        rejected += 1
                        errors.append(
                            f"Target link '{pkt.target_id}' not found in network links for measurement 'flow_m3h'"
                        )
                        continue
                else:
                    rejected += 1
                    errors.append(f"Unsupported measurement_type '{pkt.measurement_type}'")
                    continue

                # 3. Unit and value range verification
                if pkt.measurement_type == "pressure_m":
                    if pkt.unit != "m" or not (-10.0 <= pkt.value <= 200.0):
                        rejected += 1
                        errors.append(
                            f"Invalid pressure_m reading: unit='{pkt.unit}' (expected 'm'), value={pkt.value} (expected -10.0..200.0)"
                        )
                        continue
                elif pkt.measurement_type == "flow_m3h":
                    if pkt.unit != "m3h" or not (-50000.0 <= pkt.value <= 50000.0):
                        rejected += 1
                        errors.append(
                            f"Invalid flow_m3h reading: unit='{pkt.unit}' (expected 'm3h'), value={pkt.value} (expected -50000.0..50000.0)"
                        )
                        continue
                elif pkt.measurement_type == "demand_lps":
                    if pkt.unit != "lps" or not (0.0 <= pkt.value <= 500.0):
                        rejected += 1
                        errors.append(
                            f"Invalid demand_lps reading: unit='{pkt.unit}' (expected 'lps'), value={pkt.value} (expected 0.0..500.0)"
                        )
                        continue

                # 4. Timestamp parsing and staleness check (older than 24h relative to source latest)
                try:
                    pkt_dt = _parse_iso8601_utc(pkt.observed_at_utc)
                except Exception as exc:
                    rejected += 1
                    errors.append(f"Invalid timestamp format '{pkt.observed_at_utc}': {exc}")
                    continue

                now_utc = datetime.now(timezone.utc)
                if (pkt_dt - now_utc).total_seconds() > 24 * 3600:
                    rejected += 1
                    errors.append(
                        f"Packet timestamp '{pkt.observed_at_utc}' is more than 24 hours in the future"
                    )
                    continue

                latest_source_dt = self.latest_source_timestamps.get(pkt.source_id)
                if latest_source_dt is not None:
                    delta_seconds = (latest_source_dt - pkt_dt).total_seconds()
                    if delta_seconds > 24 * 3600:
                        rejected += 1
                        errors.append(
                            f"Packet timestamp '{pkt.observed_at_utc}' is older than 24 hours relative to source '{pkt.source_id}' latest accepted timestamp"
                        )
                        continue
                # 5. Deduplication check
                last_seq = self.seen_sequences.get(pkt.source_id, 0)
                if pkt.sequence_id <= last_seq:
                    deduplicated += 1
                    continue

                # 6. Acceptance
                self.seen_sequences[pkt.source_id] = pkt.sequence_id
                if latest_source_dt is None or pkt_dt > latest_source_dt:
                    self.latest_source_timestamps[pkt.source_id] = pkt_dt

                self.latest_readings[pkt.target_id] = pkt
                self.buffer.append(pkt)
                accepted += 1

            return TelemetryIngestResponse(
                accepted=accepted,
                rejected=rejected,
                deduplicated=deduplicated,
                errors=errors,
                active_network_revision=active_rev,
            )

    def generate_simulated_packets(
        self,
        engine: Any,
        seed: int = 42,
        leak_node_id: str | None = None,
        leak_area_cm2: float = 25.0,
    ) -> list[TelemetryPacket]:
        """Deterministic, seeded packet generation for sentinel sensors and key links."""
        with self._lock:
            self._sync_revision(engine)
            active_rev = getattr(engine, "network_revision", "rev-unknown")
            now_iso = datetime.now(timezone.utc).isoformat()
            rng = np.random.default_rng(seed)

            # Advance internal seed for smooth sequential stepping
            self.seed = seed + 1

            packets: list[TelemetryPacket] = []

            # 1. Pressure readings at sentinel sensors
            sensors = list(getattr(engine, "sensor_ids", []))
            pressures: dict[str, float] = {}

            if leak_node_id and hasattr(engine, "_solve_with_leak") and leak_node_id in getattr(engine, "junctions", []):
                try:
                    p_clean, leak_m3s = engine._solve_with_leak(leak_node_id, leak_area_cm2)
                    for s_id, p_val in zip(sensors, p_clean):
                        pressures[s_id] = float(p_val)
                except Exception as exc:
                    log.warning("generate_simulated_packets: _solve_with_leak failed (%s), falling back to baseline", exc)
                    pressures = {s: float(getattr(engine, "baseline_pressure", {}).get(s, 35.0)) for s in sensors}
            else:
                base_press = getattr(engine, "baseline_pressure", {})
                pressures = {s: float(base_press.get(s, 35.0)) for s in sensors}

            for s_id, base_p in pressures.items():
                noise = float(rng.normal(0.0, 0.05))
                val = round(max(0.0, min(200.0, base_p + noise)), 2)
                src_id = f"sim_press_{s_id}"
                packets.append(
                    TelemetryPacket(
                        source_id=src_id,
                        source_type="simulator",
                        network_revision=active_rev,
                        observed_at_utc=now_iso,
                        sequence_id=self._next_sim_seq(src_id),
                        target_id=s_id,
                        measurement_type="pressure_m",
                        value=val,
                        unit="m",
                        quality="SIMULATED",
                    )
                )

            # 2. Flow readings on key source / feeder links
            source_links: list[str] = []
            sources = getattr(engine, "sources", [])
            graph = getattr(engine, "graph", None)
            if graph is not None:
                for src in sources:
                    if src in graph:
                        for _, _, d in graph.edges(src, data=True):
                            lid = d.get("link") or d.get("name")
                            if lid and lid not in source_links:
                                source_links.append(lid)

            wn = getattr(engine, "wn", None)
            if not source_links and wn is not None:
                source_links = list(getattr(wn, "link_name_list", []))[:3]

            base_flows = getattr(engine, "baseline_flow_m3h", {})
            for lid in source_links[:3]:
                base_q = float(base_flows.get(lid, 120.0))
                noise_q = float(rng.normal(0.0, 0.2))
                val_q = round(max(-50000.0, min(50000.0, base_q + noise_q)), 2)
                src_id = f"sim_flow_{lid}"
                packets.append(
                    TelemetryPacket(
                        source_id=src_id,
                        source_type="simulator",
                        network_revision=active_rev,
                        observed_at_utc=now_iso,
                        sequence_id=self._next_sim_seq(src_id),
                        target_id=lid,
                        measurement_type="flow_m3h",
                        value=val_q,
                        unit="m3h",
                        quality="SIMULATED",
                    )
                )

            # 3. Demand packet if leak node simulated
            if leak_node_id and leak_node_id in getattr(engine, "junctions", []):
                src_id = f"sim_demand_{leak_node_id}"
                # Approximate 25 cm² leak at ~40m ≈ 100-150 L/s
                leak_lps = round(float(25.0 * 5.0 + float(rng.normal(0.0, 0.5))), 2)
                packets.append(
                    TelemetryPacket(
                        source_id=src_id,
                        source_type="simulator",
                        network_revision=active_rev,
                        observed_at_utc=now_iso,
                        sequence_id=self._next_sim_seq(src_id),
                        target_id=leak_node_id,
                        measurement_type="demand_lps",
                        value=max(0.0, min(500.0, leak_lps)),
                        unit="lps",
                        quality="SIMULATED",
                    )
                )

            return packets

    # ─────────────────────────── Pipe Condition & RUL ───────────────────────────

    def get_pipe_conditions(self, engine: Any) -> PipeConditionResponse:
        """Compute physical hydraulic load metrics and honest asset condition for all network pipes."""
        with self._lock:
            self._sync_revision(engine)
            active_rev = getattr(engine, "network_revision", "rev-unknown")

            wn = getattr(engine, "wn", None)
            if wn is None:
                raise HTTPException(status_code=500, detail="WaterNetworkModel not initialized in engine")

            pipe_names = getattr(wn, "pipe_name_list", None)
            if pipe_names is None:
                pipe_names = [
                    l for l in getattr(wn, "link_name_list", [])
                    if getattr(wn.get_link(l), "link_type", "") == "Pipe"
                ]

            meta = getattr(engine, "meta", {})
            pipes_meta = meta.get("pipes", {}) if isinstance(meta, dict) else {}
            base_flows = getattr(engine, "baseline_flow_m3h", {})
            base_pressures = getattr(engine, "baseline_pressure", {})

            pipe_items: list[PipeConditionItem] = []
            standard_missing_inputs = [
                "material",
                "installation_year",
                "wall_thickness_mm",
                "repair_history",
                "transient_wave_speed_ms",
            ]

            for pid in pipe_names:
                link = wn.get_link(pid)
                from_node = str(link.start_node_name)
                to_node = str(link.end_node_name)

                # Geometry
                p_meta = pipes_meta.get(pid, {}) if isinstance(pipes_meta, dict) else {}
                diameter_m = float(getattr(link, "diameter", 0.15) or 0.15)
                diameter_mm = float(p_meta.get("diameter_mm", diameter_m * 1000.0))
                length_m = float(p_meta.get("length_m", getattr(link, "length", 100.0) or 100.0))
                roughness = float(getattr(link, "roughness", 130.0) or 130.0)

                # Hydraulic state
                flow_m3h = float(base_flows.get(pid, 0.0))
                q_m3s = abs(flow_m3h) / 3600.0
                d_m = diameter_mm / 1000.0
                area_m2 = math.pi * (d_m / 2.0) ** 2

                velocity_ms = round(q_m3s / area_m2, 3) if area_m2 > 0 else 0.0

                # Hazen-Williams unit headloss (m / km)
                # S = 10.67 * (Q / C)^1.852 * D^-4.8704  [m / m]
                if q_m3s > 1e-9 and d_m > 1e-6 and roughness > 0:
                    unit_hf = 10.67 * ((q_m3s / roughness) ** 1.852) * (d_m ** -4.8704)
                    headloss_m_per_km = round(unit_hf * 1000.0, 3)
                else:
                    headloss_m_per_km = 0.0

                # Illustrative hydraulic stress index (bounded [0.0, 1.0])
                v_norm = min(1.0, max(0.0, velocity_ms / 2.5))
                h_norm = min(1.0, max(0.0, headloss_m_per_km / 10.0))
                illustrative_stress_index = round(min(1.0, max(0.0, 0.6 * v_norm + 0.4 * h_norm)), 3)

                # Check for verified structural records
                has_wall_thickness = p_meta.get("wall_thickness_mm") is not None
                has_material = p_meta.get("material") is not None
                has_age = p_meta.get("age_years") is not None or p_meta.get("installation_year") is not None

                if has_wall_thickness and has_material and has_age:
                    rul_status = "estimated"
                    t_mm = float(p_meta["wall_thickness_mm"])
                    mat_str = str(p_meta["material"]).lower()
                    age_years = float(p_meta.get("age_years", 0.0))

                    p_from = float(base_pressures.get(from_node, 0.0))
                    p_to = float(base_pressures.get(to_node, 0.0))
                    p_avg_m = max(0.0, (p_from + p_to) / 2.0)
                    p_mpa = p_avg_m * 0.00980665

                    # Thin-wall hoop stress sigma_h = (P * D) / (2 * t)
                    hoop_stress_mpa = round((p_mpa * diameter_mm) / (2.0 * t_mm), 2) if t_mm > 0 else 0.0

                    wave_speed = float(p_meta.get("transient_wave_speed_ms", 1000.0))
                    joukowsky_surge_m = round((wave_speed * velocity_ms) / 9.80665, 2)

                    design_lifespans = {
                        "ductile_iron": 80.0,
                        "cast_iron": 75.0,
                        "steel": 70.0,
                        "pvc": 50.0,
                        "hdpe": 50.0,
                        "concrete": 60.0,
                    }
                    d_life = design_lifespans.get(mat_str, 60.0)
                    effective_age = age_years * (1.0 + 0.5 * illustrative_stress_index)
                    rul_years = round(max(0.0, d_life - effective_age), 1)

                    missing_inputs = [inp for inp in standard_missing_inputs if inp not in p_meta]
                    provenance_note = f"Estimated RUL and hoop stress based on provided {mat_str} asset records."
                else:
                    rul_status = "unavailable"
                    rul_years = None
                    hoop_stress_mpa = None
                    joukowsky_surge_m = None
                    missing_inputs = list(standard_missing_inputs)
                    provenance_note = (
                        "Illustrative hydraulic load index derived from steady-state velocity and headloss. "
                        "Not a certified structural failure prediction or RUL due to missing physical asset attributes "
                        "(material, installation year, wall thickness, soil corrosivity, and repair history)."
                    )

                pipe_items.append(
                    PipeConditionItem(
                        pipe_id=pid,
                        from_node=from_node,
                        to_node=to_node,
                        diameter_mm=round(diameter_mm, 2),
                        length_m=round(length_m, 2),
                        flow_m3h=round(flow_m3h, 2),
                        velocity_ms=velocity_ms,
                        headloss_m_per_km=headloss_m_per_km,
                        rul_status=rul_status,
                        rul_years=rul_years,
                        hoop_stress_mpa=hoop_stress_mpa,
                        joukowsky_surge_m=joukowsky_surge_m,
                        illustrative_stress_index=illustrative_stress_index,
                        missing_inputs=missing_inputs,
                        provenance_note=provenance_note,
                    )
                )

            rul_available = any(p.rul_status == "estimated" for p in pipe_items)
            methodology_note = (
                "Municipal pipe RUL and structural health require certified pipe age, wall thickness, material class, "
                "and repair records. For pipes without structural records, HydroPulse provides an illustrative hydraulic "
                "stress index (0.0-1.0) based on velocity and headloss per km rather than fabricating unverified lifespans."
            )

            return PipeConditionResponse(
                network_revision=active_rev,
                rul_available=rul_available,
                methodology_note=methodology_note,
                pipes=pipe_items,
            )

    # ─────────────────────────── Auto-Valve Simulation ───────────────────────────

    def handle_auto_valve(self, engine: Any, req: AutoValveRequest) -> AutoValveResponse:
        """Human-in-the-loop, simulation-only automated valve isolation management."""
        with self._lock:
            self._sync_revision(engine)
            active_rev = getattr(engine, "network_revision", "rev-unknown")

            # Check expected network revision
            if req.expected_network_revision is not None and req.expected_network_revision != active_rev:
                raise HTTPException(
                    status_code=409,
                    detail=f"Stale network_revision: expected '{req.expected_network_revision}', active '{active_rev}'",
                )

            action = req.action

            # 1. Emergency Stop
            if action == "emergency_stop":
                self.emergency_stop = True
                self.auto_valve_enabled = False
                self.pending_valve_proposal = None
                self.active_valve_closures = []
                return AutoValveResponse(
                    network_revision=active_rev,
                    auto_valve_enabled=self.auto_valve_enabled,
                    emergency_stop=self.emergency_stop,
                    action_taken="emergency_stop",
                    status="stopped",
                    proposal=None,
                    active_valve_closures=[],
                    simulation_only=True,
                    message="Emergency stop activated. Auto-valve mode disabled, pending proposals cleared, and all valves reset to OPEN.",
                )

            # 2. Reset Valves
            if action == "reset_valves":
                self.emergency_stop = False
                self.active_valve_closures = []
                self.pending_valve_proposal = None
                return AutoValveResponse(
                    network_revision=active_rev,
                    auto_valve_enabled=self.auto_valve_enabled,
                    emergency_stop=self.emergency_stop,
                    action_taken="reset_valves",
                    status="reset",
                    proposal=None,
                    active_valve_closures=[],
                    simulation_only=True,
                    message="All valve closures reset to OPEN in simulation. Pending proposals cleared.",
                )

            # 3. Set Mode
            if action == "set_mode":
                if self.emergency_stop and req.enable_auto_valve:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=False,
                        emergency_stop=True,
                        action_taken="set_mode",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: Emergency stop is active. Reset valves before enabling auto-valve mode.",
                    )
                if req.enable_auto_valve is not None:
                    self.auto_valve_enabled = bool(req.enable_auto_valve)
                return AutoValveResponse(
                    network_revision=active_rev,
                    auto_valve_enabled=self.auto_valve_enabled,
                    emergency_stop=self.emergency_stop,
                    action_taken="set_mode",
                    status="idle",
                    proposal=self.pending_valve_proposal,
                    active_valve_closures=list(self.active_valve_closures),
                    simulation_only=True,
                    message=f"Auto-valve mode set to {'ENABLED' if self.auto_valve_enabled else 'DISABLED'}.",
                )

            # 4. Propose
            if action == "propose":
                if not req.target_node_id or req.target_node_id not in getattr(engine, "junctions", []):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Target junction '{req.target_node_id}' not found in active network.",
                    )

                # Execute leak simulation & isolation plan
                try:
                    triang = engine.simulate_and_triangulate(
                        req.target_node_id, req.leak_area_cm2, noise_pct=0.5, seed=42
                    )
                    valves_to_close = list(triang.get("recommended_valves_to_close", []))
                    isolated_nodes = list(triang.get("isolated_node_ids", []))
                    affected_hh = int(triang.get("affected_households_after_isolation", 0))

                    iso_sim = engine.simulate_isolation(valves_to_close, req.target_node_id, req.leak_area_cm2)
                    min_p_after = float(iso_sim.get("min_pressure_m", 0.0))
                    nodes_no_supply = list(iso_sim.get("nodes_without_supply", []))
                except HTTPException:
                    raise
                except Exception as exc:
                    log.exception("simulate_and_triangulate or simulate_isolation failed during proposal")
                    raise HTTPException(status_code=422, detail=f"Hydraulic simulation failed during proposal: {exc}")

                proposal = {
                    "proposal_id": f"prop-{uuid.uuid4().hex[:8]}",
                    "network_revision": active_rev,
                    "target_node_id": req.target_node_id,
                    "leak_area_cm2": req.leak_area_cm2,
                    "valves_to_close": valves_to_close,
                    "isolated_node_ids": isolated_nodes,
                    "affected_households": affected_hh,
                    "min_pressure_after_m": min_p_after,
                    "nodes_without_supply": nodes_no_supply,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "confirmed": False,
                }
                self.pending_valve_proposal = proposal

                return AutoValveResponse(
                    network_revision=active_rev,
                    auto_valve_enabled=self.auto_valve_enabled,
                    emergency_stop=self.emergency_stop,
                    action_taken="propose",
                    status="proposed",
                    proposal=proposal,
                    active_valve_closures=list(self.active_valve_closures),
                    simulation_only=True,
                    message=f"Isolation proposal created for {req.target_node_id}: closing {len(valves_to_close)} valve(s).",
                )

            # 5. Confirm Close
            if action == "confirm_close":
                # Safety Gate 1: Emergency Stop
                if self.emergency_stop:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: Emergency stop is active. Reset emergency stop before confirming valve closures.",
                    )

                # Safety Gate 2: Auto-valve enabled
                if not self.auto_valve_enabled:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: Auto-valve mode is OFF by default. It must be explicitly enabled before confirming closure.",
                    )

                # Safety Gate 3: Human confirmation
                if not req.confirm:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: Explicit human confirmation (confirm=True) is required to execute simulated valve closure.",
                    )

                # Safety Gate 4: Valid proposal exists and matches proposal_id
                if self.pending_valve_proposal is None:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=None,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: No pending valve proposal found. An isolation proposal must be created first.",
                    )

                if not req.proposal_id or req.proposal_id != self.pending_valve_proposal.get("proposal_id"):
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message=f"Refused: Proposal ID '{req.proposal_id}' does not match active proposal '{self.pending_valve_proposal.get('proposal_id')}'.",
                    )
                if self.pending_valve_proposal.get("confirmed", False):
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message="Refused: Proposal has already been confirmed and executed.",
                    )

                # Safety Gate 5: Revision consistency
                proposal_rev = self.pending_valve_proposal.get("network_revision")
                if proposal_rev != active_rev:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message=f"Refused: Network revision changed from '{proposal_rev}' to '{active_rev}' since proposal was created.",
                    )

                # Safety Gate 6: Safety threshold refusal (excessive supply loss or min pressure collapse)
                min_p = float(self.pending_valve_proposal.get("min_pressure_after_m", 0.0))
                no_supply = self.pending_valve_proposal.get("nodes_without_supply", [])
                junction_count = len(getattr(engine, "junctions", []))
                loss_ratio = len(no_supply) / max(junction_count, 1)

                if min_p < MINIMUM_PRESSURE_M or loss_ratio > 0.5:
                    return AutoValveResponse(
                        network_revision=active_rev,
                        auto_valve_enabled=self.auto_valve_enabled,
                        emergency_stop=self.emergency_stop,
                        action_taken="confirm_close",
                        status="refused",
                        proposal=self.pending_valve_proposal,
                        active_valve_closures=list(self.active_valve_closures),
                        simulation_only=True,
                        message=(
                            f"Refused: Safety threshold violation. Closure would cause excessive supply loss "
                            f"(unsupplied junctions: {len(no_supply)}/{junction_count} ({loss_ratio:.1%}), "
                            f"min pressure: {min_p:.2f}m < {MINIMUM_PRESSURE_M:.1f}m threshold)."
                        ),
                    )

                # All safety gates passed: execute simulated closure
                closures = list(self.pending_valve_proposal.get("valves_to_close", []))
                self.active_valve_closures = closures
                self.pending_valve_proposal["confirmed"] = True
                self.pending_valve_proposal["confirmed_at_utc"] = datetime.now(timezone.utc).isoformat()

                return AutoValveResponse(
                    network_revision=active_rev,
                    auto_valve_enabled=self.auto_valve_enabled,
                    emergency_stop=self.emergency_stop,
                    action_taken="confirm_close",
                    status="closed_in_simulation",
                    proposal=self.pending_valve_proposal,
                    active_valve_closures=list(self.active_valve_closures),
                    simulation_only=True,
                    message=f"Simulated closure confirmed. {len(closures)} valve(s) closed in digital twin simulation.",
                )

            raise HTTPException(status_code=422, detail=f"Unsupported auto-valve action '{action}'")


# Singleton instance and module-level functional API
iot_manager = IoTManager()


def get_state(engine: Any, generate_demo_tick: bool = False, seed: int | None = None) -> IoTStateResponse:
    return iot_manager.get_state(engine, generate_demo_tick=generate_demo_tick, seed=seed)


def ingest(engine: Any, req: TelemetryIngestRequest) -> TelemetryIngestResponse:
    return iot_manager.ingest(engine, req)


def generate_simulated_packets(
    engine: Any, seed: int = 42, leak_node_id: str | None = None, leak_area_cm2: float = 25.0
) -> list[TelemetryPacket]:
    return iot_manager.generate_simulated_packets(
        engine, seed=seed, leak_node_id=leak_node_id, leak_area_cm2=leak_area_cm2
    )


def get_pipe_conditions(engine: Any) -> PipeConditionResponse:
    return iot_manager.get_pipe_conditions(engine)


def handle_auto_valve(engine: Any, req: AutoValveRequest) -> AutoValveResponse:
    return iot_manager.handle_auto_valve(engine, req)
