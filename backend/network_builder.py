"""
Network Builder Manager for HydroPulse v1.1.
Handles network import (EPANET .inp and GeoJSON), presets catalog,
map builder preview & commit, network reset/rollback, and hydraulic IntelliSense.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import networkx as nx
from fastapi import HTTPException
import wntr

from backend.hydraulic_engine import (
    LPCD,
    MINIMUM_PRESSURE_M,
    PERSONS_PER_HOUSEHOLD,
    PRESSURE_EXPONENT,
    REQUIRED_PRESSURE_M,
    TANKER_LITRES,
)
from data_utils import geo_mapper
from data_utils.geo_mapper import haversine_m, map_network_coordinates, network_center
from data_utils.schemas import (
    BuildProposedNode,
    BuildProposedPipe,
    IntelliSenseRequest,
    IntelliSenseResponse,
    NetworkBuildCommitRequest,
    NetworkBuildPreviewRequest,
    NetworkBuildPreviewResponse,
    NetworkImportCommitRequest,
    NetworkImportPreviewRequest,
    NetworkImportPreviewResponse,
    NetworkPresetInfo,
    NetworkPresetsListResponse,
    NetworkResetRequest,
    NetworkRevisionCommitResponse,
    NetworkRevisionMeta,
    WhatIfMetrics,
)

NOMINAL_DIAMETERS_MM = [110, 150, 200, 250, 300, 400]
MAX_IMPORT_CHARS = 2_000_000


def _extract_coords(wn: Any) -> Dict[str, Tuple[float, float]]:
    """Extract (lat, lng) mapping from WaterNetworkModel with geo_mapper fallback."""
    if hasattr(geo_mapper, "extract_node_latlngs"):
        return geo_mapper.extract_node_latlngs(wn)
    return map_network_coordinates(wn)


def _clone_wn(engine: Any) -> wntr.network.WaterNetworkModel:
    """Clone engine's WaterNetworkModel safely."""
    if hasattr(engine, "clone_for_scenario"):
        res = engine.clone_for_scenario()
        if isinstance(res, tuple):
            return copy.deepcopy(res[0])
        return copy.deepcopy(res)
    return copy.deepcopy(engine.wn)


def _compute_metrics(
    pressures: Dict[str, float],
    hh_map: Dict[str, int],
    total_leak_loss_lpm: float = 0.0,
) -> WhatIfMetrics:
    """Calculate WhatIfMetrics summary from junction pressures and household counts."""
    if not pressures:
        return WhatIfMetrics(
            min_pressure_m=0.0,
            avg_pressure_m=0.0,
            max_pressure_m=0.0,
            pressure_deficit_nodes=0,
            unsupplied_nodes=[],
            affected_households=0,
            total_leak_loss_lpm=round(total_leak_loss_lpm, 2),
            tankers_lost_per_day=round(total_leak_loss_lpm * 1440.0 / TANKER_LITRES, 1),
        )

    vals = list(pressures.values())
    min_p = round(float(min(vals)), 2)
    avg_p = round(float(sum(vals) / len(vals)), 2)
    max_p = round(float(max(vals)), 2)
    deficit_nodes = [nid for nid, p in pressures.items() if p < REQUIRED_PRESSURE_M]
    unsupplied_nodes = [nid for nid, p in pressures.items() if p < MINIMUM_PRESSURE_M]
    affected_hh = sum(hh_map.get(nid, 1) for nid in deficit_nodes)
    tankers = round(total_leak_loss_lpm * 1440.0 / TANKER_LITRES, 1)

    return WhatIfMetrics(
        min_pressure_m=min_p,
        avg_pressure_m=avg_p,
        max_pressure_m=max_p,
        pressure_deficit_nodes=len(deficit_nodes),
        unsupplied_nodes=sorted(unsupplied_nodes),
        affected_households=affected_hh,
        total_leak_loss_lpm=round(total_leak_loss_lpm, 2),
        tankers_lost_per_day=tankers,
    )


def _build_revision_meta(engine: Any, meta_dict: Dict[str, Any] | None = None) -> NetworkRevisionMeta:
    """Construct a validated NetworkRevisionMeta model from engine snapshot or meta_dict."""
    if meta_dict is None:
        if hasattr(engine, "revision_meta_dict"):
            meta_dict = engine.revision_meta_dict()
        elif hasattr(engine, "get_revision_meta"):
            meta_dict = engine.get_revision_meta()
        else:
            meta_dict = {}

    d = dict(meta_dict)
    if "network_id" not in d:
        d["network_id"] = getattr(engine, "network_id", "network")
    if "network_revision" not in d:
        d["network_revision"] = getattr(engine, "network_revision", "rev-unknown")
    if (
        "source_type" not in d
        or d["source_type"]
        not in ("synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback")
    ):
        st = getattr(engine, "source_type", "synthetic")
        d["source_type"] = st if st in (
            "synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback"
        ) else "synthetic"
    if "source_filename" not in d:
        d["source_filename"] = getattr(engine, "source_filename", None)
    if "sha256" not in d:
        d["sha256"] = getattr(engine, "sha256", getattr(engine, "network_sha256", None))
    if "crs" not in d:
        d["crs"] = "EPSG:4326"
    if "created_at_utc" not in d:
        d["created_at_utc"] = getattr(engine, "updated_at_utc", datetime.now(timezone.utc).isoformat())
    if "num_nodes" not in d:
        d["num_nodes"] = len(engine.wn.node_name_list) if hasattr(engine, "wn") else 1
    if "num_pipes" not in d:
        d["num_pipes"] = len(engine.wn.pipe_name_list) if hasattr(engine, "wn") else 1
    if "can_rollback" not in d:
        d["can_rollback"] = bool(getattr(engine, "_previous_snapshot", None) is not None)
    if "validation_summary" not in d:
        d["validation_summary"] = {}

    return NetworkRevisionMeta(**d)


def _validate_filename(filename: str) -> Optional[str]:
    """Check for NUL bytes, traversal sequences, or absolute path prefixes."""
    if "\x00" in filename:
        return "Invalid filename: null byte detected."
    if ".." in filename or filename.startswith("/") or filename.startswith("\\") or "\\" in filename:
        return "Invalid filename: path traversal sequence detected."
    if Path(filename).name != filename:
        return "Invalid filename: directory separator detected."
    return None


class NetworkBuilderManager:
    """Manages network imports, presets catalogue, builder preview/commit, rollback, and IntelliSense."""

    def __init__(self) -> None:
        self._preview_cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _prune_cache(self) -> None:
        """Evict preview cache items older than 3600 seconds or cap size to 100."""
        now = time.time()
        expired = [k for k, v in self._preview_cache.items() if now - v.get("created_at", 0) > 3600]
        for k in expired:
            self._preview_cache.pop(k, None)
        if len(self._preview_cache) > 100:
            sorted_keys = sorted(
                self._preview_cache.keys(),
                key=lambda k: self._preview_cache[k].get("created_at", 0),
            )
            for k in sorted_keys[: len(self._preview_cache) - 100]:
                self._preview_cache.pop(k, None)

    # ─────────────────────────── 1. Presets ───────────────────────────
    def list_network_presets(self, engine: Any) -> NetworkPresetsListResponse:
        """
        List available and uncommitted preset networks.
        Honest catalog: kengeri_baseline is available; jayanagar_dma and indiranagar_dma
        are explicitly flagged unavailable until verified assets are added.
        """
        source_type_val = getattr(engine, "source_type", None)
        if (
            not source_type_val
            or source_type_val
            not in ("synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback")
        ):
            meta_params = engine.meta.get("params", {}) if isinstance(engine.meta, dict) else {}
            source_type_val = meta_params.get("source", "synthetic")
        if source_type_val not in ("synthetic", "osm", "imported_inp", "imported_geojson", "ward_preset", "net3_fallback"):
            source_type_val = "synthetic"

        kengeri = NetworkPresetInfo(
            preset_id="kengeri_baseline",
            name="Kengeri Baseline DMA",
            source_type=source_type_val,
            available=True,
            num_nodes=len(engine.wn.node_name_list),
            num_pipes=len(engine.wn.pipe_name_list),
            description="Bengaluru Kengeri 130-node DMA model generated from OpenStreetMap road topology with synthetic PDD hydraulics.",
            provenance="Committed repository asset (backend/bangalore_kengeri.inp + network_meta.json)",
        )
        jayanagar = NetworkPresetInfo(
            preset_id="jayanagar_dma",
            name="Jayanagar 4th Block DMA",
            source_type="ward_preset",
            available=False,
            num_nodes=0,
            num_pipes=0,
            description="Ward dataset not committed in repository; unavailable until verified .inp/GeoJSON asset is provided.",
            provenance="unverified / unavailable",
        )
        indiranagar = NetworkPresetInfo(
            preset_id="indiranagar_dma",
            name="Indiranagar 100ft Road DMA",
            source_type="ward_preset",
            available=False,
            num_nodes=0,
            num_pipes=0,
            description="Ward dataset not committed in repository; unavailable until verified .inp/GeoJSON asset is provided.",
            provenance="unverified / unavailable",
        )

        return NetworkPresetsListResponse(
            active_network_revision=engine.network_revision,
            presets=[kengeri, jayanagar, indiranagar],
        )

    # ─────────────────────────── 2. Import Preview ───────────────────────────
    def preview_import(
        self, engine: Any, req: NetworkImportPreviewRequest
    ) -> NetworkImportPreviewResponse:
        """
        Validate EPANET .inp or GeoJSON network upload.
        Enforces size, safety, graph topology, minimum elements, and PDD convergence.
        Stores valid candidate in memory and returns preview token.
        """
        if len(req.content) > MAX_IMPORT_CHARS:
            return NetworkImportPreviewResponse(
                valid=False,
                preview_token=None,
                source_network_revision=engine.network_revision,
                proposed_meta=None,
                warnings=[],
                errors=[f"File content exceeds maximum permitted size of {MAX_IMPORT_CHARS:,} characters."],
            )

        fn_err = _validate_filename(req.filename)
        if fn_err:
            return NetworkImportPreviewResponse(
                valid=False,
                preview_token=None,
                source_network_revision=engine.network_revision,
                proposed_meta=None,
                warnings=[],
                errors=[fn_err],
            )

        warnings: List[str] = []
        errors: List[str] = []

        if req.format == "inp":
            wn, meta, warnings, errors = self._parse_inp(req, warnings, errors)
        elif req.format == "geojson":
            wn, meta, warnings, errors = self._parse_geojson(req, warnings, errors)
        else:
            errors.append(f"Unsupported format '{req.format}'. Supported formats: 'inp', 'geojson'.")
            wn, meta = None, None

        if errors or wn is None or meta is None:
            return NetworkImportPreviewResponse(
                valid=False,
                preview_token=None,
                source_network_revision=engine.network_revision,
                proposed_meta=None,
                warnings=warnings,
                errors=errors,
            )

        sha256 = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
        token = f"imp-{uuid4().hex[:12]}"
        st_type = "imported_inp" if req.format == "inp" else "imported_geojson"

        with self._lock:
            self._prune_cache()
            self._preview_cache[token] = {
                "type": "import",
                "wn": wn,
                "meta": meta,
                "source_type": st_type,
                "filename": req.filename,
                "sha256": sha256,
                "source_network_revision": engine.network_revision,
                "created_at": time.time(),
            }

        proposed_meta = NetworkRevisionMeta(
            network_id=str(meta.get("network_name", "imported_network")),
            network_revision=f"proposed-imp-{sha256[:8]}",
            source_type=st_type,
            source_filename=req.filename,
            sha256=sha256,
            crs="EPSG:4326",
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            num_nodes=len(wn.node_name_list),
            num_pipes=len(wn.pipe_name_list),
            can_rollback=True,
            validation_summary={
                "junctions": len(wn.junction_name_list),
                "reservoirs": len(wn.reservoir_name_list),
                "tanks": len(wn.tank_name_list),
                "pipes": len(wn.pipe_name_list),
                "format": req.format,
            },
        )

        return NetworkImportPreviewResponse(
            valid=True,
            preview_token=token,
            source_network_revision=engine.network_revision,
            proposed_meta=proposed_meta,
            warnings=warnings,
            errors=[],
        )

    def _parse_inp(
        self,
        req: NetworkImportPreviewRequest,
        warnings: List[str],
        errors: List[str],
    ) -> Tuple[Optional[wntr.network.WaterNetworkModel], Optional[Dict[str, Any]], List[str], List[str]]:
        """Parse EPANET INP file from temporary directory and validate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, "network.inp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(req.content)
                wn = wntr.network.WaterNetworkModel(tmp_path)
            except Exception as e:
                errors.append(f"EPANET INP parsing failed: {e}")
                return None, None, warnings, errors

        reservoirs = list(wn.reservoir_name_list)
        tanks = list(wn.tank_name_list)
        junctions = list(wn.junction_name_list)
        pipes = list(wn.pipe_name_list)

        if len(reservoirs) + len(tanks) < 1:
            errors.append("Model must contain at least 1 reservoir or tank.")
        if len(junctions) < 5:
            errors.append(f"Model must contain at least 5 junctions (found {len(junctions)}).")
        if len(junctions) > 500:
            errors.append(f"Model exceeds maximum allowed 500 junctions (found {len(junctions)}).")
        if len(pipes) < 5:
            errors.append(f"Model must contain at least 5 pipes (found {len(pipes)}).")
        if len(pipes) > 750:
            errors.append(f"Model exceeds maximum allowed 750 pipes (found {len(pipes)}).")

        non_pos_diam = [p for p in pipes if getattr(wn.get_link(p), "diameter", 0.0) <= 0]
        non_pos_len = [p for p in pipes if getattr(wn.get_link(p), "length", 0.0) <= 0]
        if non_pos_diam:
            errors.append(f"Found {len(non_pos_diam)} pipe(s) with non-positive diameter.")
        if non_pos_len:
            errors.append(f"Found {len(non_pos_len)} pipe(s) with non-positive length.")

        g = nx.Graph()
        g.add_nodes_from(wn.node_name_list)
        for p in pipes:
            link = wn.get_link(p)
            g.add_edge(link.start_node_name, link.end_node_name)

        components = list(nx.connected_components(g))
        if len(components) > 1:
            main_comp = max(components, key=len)
            minor_nodes = sum(len(c) for c in components if c != main_comp)
            if minor_nodes <= 5:
                warnings.append(
                    f"Network has {len(components)} disconnected components ({minor_nodes} minor fragment nodes)."
                )
            else:
                errors.append(
                    f"Network has {len(components)} disconnected components ({minor_nodes} disconnected nodes). Single connected component required."
                )

        if errors:
            return None, None, warnings, errors

        # Apply PDD invariants
        wn.options.time.duration = 0
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
        wn.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
        wn.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

        try:
            sim = wntr.sim.WNTRSimulator(wn)
            results = sim.run_sim()
            pressures = results.node["pressure"].iloc[0]
            if pressures.isna().any():
                errors.append("Steady-state PDD simulation returned NaN pressures.")
        except Exception as sim_err:
            errors.append(f"Steady-state PDD simulation failed to converge: {sim_err}")

        if errors:
            return None, None, warnings, errors

        latlngs = _extract_coords(wn)
        c_lat, c_lng = network_center(latlngs)

        nodes_meta: Dict[str, Any] = {}
        hh_meta: Dict[str, int] = {}
        for j in junctions:
            node = wn.get_node(j)
            elev = float(getattr(node, "elevation", 800.0))
            demand = float(getattr(node, "base_demand", 0.00025))
            base_lpd = demand * 1000.0 * 86400.0
            hh = int(min(max(round(base_lpd / (LPCD * PERSONS_PER_HOUSEHOLD)), 1), 500)) if demand > 0 else 1
            nodes_meta[j] = {
                "elevation_m": elev,
                "base_demand_lps": round(demand * 1000.0, 3),
                "households": hh,
            }
            hh_meta[j] = hh

        for r in reservoirs:
            node = wn.get_node(r)
            head = float(getattr(node, "base_head", getattr(node, "head", 850.0)))
            nodes_meta[r] = {"elevation_m": head, "base_demand_lps": 0.0, "households": 0}

        for t in tanks:
            node = wn.get_node(t)
            elev = float(getattr(node, "elevation", 850.0))
            nodes_meta[t] = {"elevation_m": elev, "base_demand_lps": 0.0, "households": 0}

        pipes_meta: Dict[str, Any] = {}
        road_class_meta: Dict[str, str] = {}
        for p in pipes:
            link = wn.get_link(p)
            diam_mm = round(float(link.diameter * 1000.0), 1)
            len_m = round(float(link.length), 2)
            pipes_meta[p] = {"road_class": "residential", "diameter_mm": diam_mm, "length_m": len_m}
            road_class_meta[p] = "residential"

        net_name = req.network_name or Path(req.filename).stem or "imported_network"
        meta: Dict[str, Any] = {
            "network_name": net_name,
            "center": {"lat": c_lat, "lng": c_lng},
            "nodes": nodes_meta,
            "pipes": pipes_meta,
            "households": hh_meta,
            "road_class": road_class_meta,
            "params": {
                "source": "imported_inp",
                "filename": req.filename,
                "lat": c_lat,
                "lng": c_lng,
            },
        }

        return wn, meta, warnings, errors

    def _parse_geojson(
        self,
        req: NetworkImportPreviewRequest,
        warnings: List[str],
        errors: List[str],
    ) -> Tuple[Optional[wntr.network.WaterNetworkModel], Optional[Dict[str, Any]], List[str], List[str]]:
        """Parse GeoJSON FeatureCollection, validate WGS84 CRS, connectivity and build model."""
        try:
            data = json.loads(req.content)
        except Exception as json_err:
            errors.append(f"Invalid JSON content: {json_err}")
            return None, None, warnings, errors

        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            errors.append("GeoJSON root must be a FeatureCollection object.")
            return None, None, warnings, errors

        crs_obj = data.get("crs")
        if crs_obj:
            crs_name = ""
            if isinstance(crs_obj, str):
                crs_name = crs_obj
            elif isinstance(crs_obj, dict):
                crs_name = str(crs_obj.get("properties", {}).get("name", crs_obj.get("type", "")))
            crs_lower = crs_name.lower()
            if not any(v in crs_lower for v in ("4326", "crs84", "wgs84", "wgs 84")):
                errors.append(f"Unsupported CRS '{crs_name}'. HydroPulse requires default WGS84 / EPSG:4326.")
                return None, None, warnings, errors

        features = data.get("features", [])
        if not isinstance(features, list):
            errors.append("FeatureCollection 'features' must be an array.")
            return None, None, warnings, errors
        if len(features) > 500:
            errors.append(f"GeoJSON feature count ({len(features)}) exceeds maximum limit of 500 features.")
            return None, None, warnings, errors
        if len(features) == 0:
            errors.append("GeoJSON FeatureCollection contains no features.")
            return None, None, warnings, errors

        nodes: Dict[str, Dict[str, Any]] = {}
        pipes: Dict[str, Dict[str, Any]] = {}
        seen_node_ids = set()
        seen_pipe_ids = set()

        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                errors.append(f"Feature at index {idx} is not an object.")
                continue
            geom = feat.get("geometry")
            if not isinstance(geom, dict):
                errors.append(f"Feature at index {idx} is missing a geometry object.")
                continue
            gtype = geom.get("type")
            coords = geom.get("coordinates")
            props = feat.get("properties") or {}

            fid = feat.get("id") or props.get("id") or props.get("node_id") or props.get("pipe_id") or props.get("name")
            if not fid:
                errors.append(f"Feature at index {idx} ({gtype}) is missing an 'id'.")
                continue
            fid = str(fid).strip()

            if gtype == "Point":
                if fid in seen_node_ids:
                    errors.append(f"Duplicate node ID '{fid}' detected at feature {idx}.")
                    continue
                seen_node_ids.add(fid)

                if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                    errors.append(f"Node '{fid}' has invalid Point coordinates: {coords}.")
                    continue
                lng, lat = float(coords[0]), float(coords[1])
                if not (math.isfinite(lat) and math.isfinite(lng)):
                    errors.append(f"Node '{fid}' has non-finite coordinates: ({lat}, {lng}).")
                    continue
                if not (12.0 <= lat <= 14.0 and 76.0 <= lng <= 79.0):
                    errors.append(
                        f"Node '{fid}' coordinates ({lat:.4f}, {lng:.4f}) outside Bengaluru bounds (lat: 12.0-14.0, lng: 76.0-79.0)."
                    )
                    continue

                role = str(props.get("role") or props.get("type") or props.get("node_type") or "junction").lower()
                if role in ("reservoir", "source"):
                    ntype = "reservoir"
                elif role in ("tank",):
                    ntype = "tank"
                else:
                    ntype = "junction"

                elev = props.get("elevation_m") or props.get("elevation") or props.get("base_head") or props.get("head")
                if elev is None and len(coords) >= 3 and math.isfinite(float(coords[2])):
                    elev = float(coords[2])
                if elev is None:
                    elev = 850.0 if ntype in ("reservoir", "tank") else 815.0
                else:
                    elev = float(elev)

                demand_lps = float(props.get("base_demand_lps", props.get("demand_lps", props.get("base_demand", 0.25))))
                if demand_lps < 0:
                    errors.append(f"Node '{fid}' has negative demand: {demand_lps}.")

                nodes[fid] = {
                    "id": fid,
                    "type": ntype,
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "elevation_m": round(elev, 2),
                    "base_demand_lps": round(demand_lps, 3),
                }

            elif gtype == "LineString":
                if fid in seen_pipe_ids:
                    errors.append(f"Duplicate pipe ID '{fid}' detected at feature {idx}.")
                    continue
                seen_pipe_ids.add(fid)

                from_node = props.get("source") or props.get("from_node") or props.get("start_node") or props.get("u")
                to_node = props.get("target") or props.get("to_node") or props.get("end_node") or props.get("v")
                if not from_node or not to_node:
                    errors.append(f"Pipe '{fid}' missing source/from_node or target/to_node property.")
                    continue
                from_node, to_node = str(from_node).strip(), str(to_node).strip()

                diam_raw = props.get("diameter_mm") or props.get("diameter") or 150.0
                try:
                    diam_mm = float(diam_raw)
                    if diam_mm < 1.0:
                        diam_mm *= 1000.0
                except (ValueError, TypeError):
                    diam_mm = 150.0
                if diam_mm <= 0:
                    errors.append(f"Pipe '{fid}' has non-positive diameter: {diam_mm} mm.")

                len_raw = props.get("length_m") or props.get("length")
                len_m = float(len_raw) if len_raw is not None else None
                if len_m is not None and len_m <= 0:
                    errors.append(f"Pipe '{fid}' has non-positive length: {len_m} m.")

                roughness = float(props.get("roughness", 130.0))
                road_class = str(props.get("road_class", "residential"))

                pipes[fid] = {
                    "id": fid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "diameter_mm": round(diam_mm, 1),
                    "length_m": round(len_m, 2) if len_m is not None else None,
                    "roughness": roughness,
                    "road_class": road_class,
                }
            else:
                warnings.append(f"Feature {idx} geometry '{gtype}' is not Point or LineString; ignored.")

        for pid, pd in pipes.items():
            u, v = pd["from_node"], pd["to_node"]
            if u == v:
                errors.append(f"Pipe '{pid}' forms a self-loop on node '{u}'.")
            if u not in nodes:
                errors.append(f"Pipe '{pid}' references unknown source node '{u}'.")
            if v not in nodes:
                errors.append(f"Pipe '{pid}' references unknown target node '{v}'.")

        num_sources = sum(1 for n in nodes.values() if n["type"] in ("reservoir", "tank"))
        num_junctions = sum(1 for n in nodes.values() if n["type"] == "junction")
        if num_sources < 1:
            errors.append("GeoJSON network must contain at least 1 reservoir or tank.")
        if num_junctions < 5:
            errors.append(f"GeoJSON network must contain at least 5 junctions (found {num_junctions}).")
        if len(pipes) < 5:
            errors.append(f"GeoJSON network must contain at least 5 pipes (found {len(pipes)}).")

        if errors:
            return None, None, warnings, errors

        wn = wntr.network.WaterNetworkModel()
        wn.options.time.duration = 0
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
        wn.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
        wn.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

        for nid, nd in nodes.items():
            if nd["type"] == "reservoir":
                wn.add_reservoir(nid, base_head=nd["elevation_m"], coordinates=(nd["lng"], nd["lat"]))
            elif nd["type"] == "tank":
                wn.add_tank(
                    nid,
                    elevation=nd["elevation_m"],
                    init_level=3.0,
                    min_level=0.5,
                    max_level=5.0,
                    diameter=10.0,
                    coordinates=(nd["lng"], nd["lat"]),
                )
            else:
                wn.add_junction(
                    nid,
                    base_demand=nd["base_demand_lps"] / 1000.0,
                    elevation=nd["elevation_m"],
                    coordinates=(nd["lng"], nd["lat"]),
                )

        for pid, pd in pipes.items():
            u_pt = nodes[pd["from_node"]]
            v_pt = nodes[pd["to_node"]]
            len_m = pd["length_m"]
            if len_m is None or len_m <= 0:
                len_m = max(5.0, haversine_m(u_pt["lat"], u_pt["lng"], v_pt["lat"], v_pt["lng"]))
            wn.add_pipe(
                pid,
                pd["from_node"],
                pd["to_node"],
                length=len_m,
                diameter=pd["diameter_mm"] / 1000.0,
                roughness=pd["roughness"],
                minor_loss=0.0,
            )

        g = nx.Graph()
        g.add_nodes_from(wn.node_name_list)
        for pid in wn.pipe_name_list:
            link = wn.get_link(pid)
            g.add_edge(link.start_node_name, link.end_node_name)

        components = list(nx.connected_components(g))
        if len(components) > 1:
            main_comp = max(components, key=len)
            minor_cnt = sum(len(c) for c in components if c != main_comp)
            if minor_cnt <= 5:
                warnings.append(
                    f"Network has {len(components)} disconnected components ({minor_cnt} minor fragment nodes)."
                )
            else:
                errors.append(
                    f"Network has {len(components)} disconnected components ({minor_cnt} disconnected nodes). Single connected component required."
                )

        if not errors:
            try:
                sim = wntr.sim.WNTRSimulator(wn)
                sim_res = sim.run_sim()
                pressures = sim_res.node["pressure"].iloc[0]
                if pressures.isna().any():
                    errors.append("Steady-state PDD simulation returned NaN pressures.")
            except Exception as sim_err:
                errors.append(f"Hydraulic PDD simulation failed to converge: {sim_err}")

        if errors:
            return None, None, warnings, errors

        nodes_meta: Dict[str, Any] = {}
        hh_meta: Dict[str, int] = {}
        for nid, nd in nodes.items():
            base_lpd = nd["base_demand_lps"] * 86400.0
            hh = int(min(max(round(base_lpd / (LPCD * PERSONS_PER_HOUSEHOLD)), 1), 500)) if nd["base_demand_lps"] > 0 else 1
            nodes_meta[nid] = {
                "elevation_m": nd["elevation_m"],
                "base_demand_lps": nd["base_demand_lps"],
                "households": hh,
            }
            if nd["type"] == "junction":
                hh_meta[nid] = hh

        pipes_meta: Dict[str, Any] = {}
        road_class_meta: Dict[str, str] = {}
        for pid, pd in pipes.items():
            l_val = pd["length_m"] or round(
                max(
                    5.0,
                    haversine_m(
                        nodes[pd["from_node"]]["lat"],
                        nodes[pd["from_node"]]["lng"],
                        nodes[pd["to_node"]]["lat"],
                        nodes[pd["to_node"]]["lng"],
                    ),
                ),
                2,
            )
            pipes_meta[pid] = {
                "road_class": pd["road_class"],
                "diameter_mm": pd["diameter_mm"],
                "length_m": l_val,
            }
            road_class_meta[pid] = pd["road_class"]

        lats = [n["lat"] for n in nodes.values()]
        lngs = [n["lng"] for n in nodes.values()]
        c_lat = round(sum(lats) / len(lats), 6)
        c_lng = round(sum(lngs) / len(lngs), 6)

        meta: Dict[str, Any] = {
            "network_name": req.network_name or Path(req.filename).stem or "imported_geojson",
            "center": {"lat": c_lat, "lng": c_lng},
            "nodes": nodes_meta,
            "pipes": pipes_meta,
            "households": hh_meta,
            "road_class": road_class_meta,
            "params": {
                "source": "imported_geojson",
                "filename": req.filename,
                "lat": c_lat,
                "lng": c_lng,
            },
        }

        return wn, meta, warnings, errors

    # ─────────────────────────── 3. Import Commit ───────────────────────────
    def commit_import(
        self, engine: Any, req: NetworkImportCommitRequest
    ) -> NetworkRevisionCommitResponse:
        """Commit an imported network snapshot previously validated via preview_import."""
        if not req.confirm:
            raise HTTPException(status_code=422, detail="Network import commit requires confirm=True.")

        with self._lock:
            prev = self._preview_cache.get(req.preview_token)

        if not prev or prev.get("type") != "import":
            raise HTTPException(status_code=404, detail=f"Preview token '{req.preview_token}' not found or expired.")

        if req.expected_network_revision != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Stale network revision: expected '{req.expected_network_revision}', active is '{engine.network_revision}'.",
            )
        if prev["source_network_revision"] != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Revision conflict: preview was created against '{prev['source_network_revision']}', active is '{engine.network_revision}'.",
            )

        prev_rev = engine.network_revision
        meta_dict: Dict[str, Any]

        if hasattr(engine, "promote_network"):
            meta_dict = engine.promote_network(
                prev["wn"],
                meta=prev["meta"],
                source_type=prev["source_type"],
                source_filename=prev["filename"],
                network_name=prev["meta"].get("network_name", "imported_network"),
                rebuild_matrix=True,
            )
        else:
            meta_dict = engine.activate_network(
                prev["wn"],
                meta=prev["meta"],
                source_type=prev["source_type"],
                source_filename=prev["filename"],
                network_name=prev["meta"].get("network_name", "imported_network"),
            )

        with self._lock:
            self._preview_cache.pop(req.preview_token, None)

        rev_meta = _build_revision_meta(engine, meta_dict)

        return NetworkRevisionCommitResponse(
            committed=True,
            previous_network_revision=prev_rev,
            new_network_revision=engine.network_revision,
            revision_meta=rev_meta,
            message=f"Successfully imported and promoted network '{engine.network_id}' to revision '{engine.network_revision}'.",
        )

    # ─────────────────────────── 4. IntelliSense ───────────────────────────
    def compute_intellisense(
        self, engine: Any, req: IntelliSenseRequest
    ) -> IntelliSenseResponse:
        """
        Fast non-mutating hydraulic & geometric calculator for map builder interactions.
        Calculates pipe length, elevations, Hazen-Williams velocity & headloss, and loop candidates.
        """
        if req.expected_network_revision and req.expected_network_revision != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Stale network revision: expected '{req.expected_network_revision}', active is '{engine.network_revision}'.",
            )

        latlngs = getattr(engine, "latlngs", getattr(engine, "latlng", {}))
        meta_nodes = engine.meta.get("nodes", {}) if isinstance(engine.meta, dict) else {}

        if req.from_node_id not in latlngs and req.from_node_id not in engine.wn.node_name_list:
            raise HTTPException(status_code=404, detail=f"Source node '{req.from_node_id}' not found in active network.")

        from_lat, from_lng = latlngs.get(req.from_node_id, (0.0, 0.0))
        elev_from = float(meta_nodes.get(req.from_node_id, {}).get("elevation_m", 800.0))
        if elev_from == 800.0:
            try:
                node = engine.wn.get_node(req.from_node_id)
                elev_from = float(getattr(node, "elevation", getattr(node, "base_head", 800.0)))
            except Exception:
                elev_from = 800.0

        warnings: List[str] = []

        if req.to_node_id:
            if req.to_node_id not in latlngs and req.to_node_id not in engine.wn.node_name_list:
                raise HTTPException(status_code=404, detail=f"Target node '{req.to_node_id}' not found in active network.")
            to_lat, to_lng = latlngs.get(req.to_node_id, (0.0, 0.0))
            elev_to = float(meta_nodes.get(req.to_node_id, {}).get("elevation_m", 800.0))
            if elev_to == 800.0:
                try:
                    t_node = engine.wn.get_node(req.to_node_id)
                    elev_to = float(getattr(t_node, "elevation", getattr(t_node, "base_head", 800.0)))
                except Exception:
                    elev_to = 800.0
            elevation_provenance = "existing_node_metadata"

            target_demand_lps = float(meta_nodes.get(req.to_node_id, {}).get("base_demand_lps", 0.25))
            if target_demand_lps <= 0.0:
                try:
                    t_node = engine.wn.get_node(req.to_node_id)
                    d_val = getattr(t_node, "base_demand", 0.00025)
                    target_demand_lps = float(d_val * 1000.0) if d_val > 0 else 0.25
                except Exception:
                    target_demand_lps = 0.25

            if hasattr(engine, "households_for"):
                est_households = engine.households_for(req.to_node_id)
            else:
                est_households = int(
                    meta_nodes.get(
                        req.to_node_id,
                        {},
                    ).get("households", max(1, round((target_demand_lps * 86400.0) / (LPCD * PERSONS_PER_HOUSEHOLD))))
                )

        else:
            if req.new_junction_lat is None or req.new_junction_lng is None:
                raise HTTPException(
                    status_code=422,
                    detail="Either 'to_node_id' or both ('new_junction_lat', 'new_junction_lng') must be provided.",
                )
            to_lat, to_lng = float(req.new_junction_lat), float(req.new_junction_lng)
            elevation_provenance = "nearest_neighbor_interpolated_estimate"
            warnings.append("Elevation was interpolated from nearest neighbor nodes; field survey recommended.")

            dists = []
            for nid, (nlat, nlng) in latlngs.items():
                d = haversine_m(to_lat, to_lng, nlat, nlng)
                nelev = float(meta_nodes.get(nid, {}).get("elevation_m", 800.0))
                dists.append((d, nelev))
            dists.sort(key=lambda x: x[0])
            nearest3 = dists[:3]
            if nearest3:
                weights = [1.0 / max(d[0], 0.1) for d in nearest3]
                total_w = sum(weights)
                elev_to = sum(w * d[1] for w, d in zip(weights, nearest3)) / total_w
            else:
                elev_to = elev_from

            target_demand_lps = 0.25
            est_households = max(1, round((target_demand_lps * 86400.0) / (LPCD * PERSONS_PER_HOUSEHOLD)))

        length_m = max(5.0, haversine_m(from_lat, from_lng, to_lat, to_lng))
        elev_from_m = round(float(elev_from), 2)
        elev_to_m = round(float(elev_to), 2)
        elev_diff_m = round(elev_to_m - elev_from_m, 2)
        slope_pct = round((elev_diff_m / length_m) * 100.0, 3)

        q_m3s = max(0.0001, target_demand_lps / 1000.0)
        v_target = req.target_velocity_ms
        d_ideal_m = math.sqrt((4.0 * q_m3s) / (math.pi * v_target))
        d_ideal_mm = d_ideal_m * 1000.0

        chosen_d_mm = NOMINAL_DIAMETERS_MM[-1]
        for d in NOMINAL_DIAMETERS_MM:
            if d >= d_ideal_mm:
                chosen_d_mm = d
                break

        d_m = chosen_d_mm / 1000.0
        area_m2 = math.pi * (d_m / 2.0) ** 2
        pred_v = round(q_m3s / area_m2, 3)

        c = req.roughness
        hf = 10.67 * length_m * (q_m3s ** 1.852) / ((c ** 1.852) * (d_m ** 4.87))
        pred_hf = round(float(hf), 3)

        # Loop candidates within 120m
        loop_candidates: List[Dict[str, Any]] = []
        target_nid = req.to_node_id
        engine_graph = getattr(engine, "graph", None)
        if engine_graph is None and hasattr(engine, "_build_graph"):
            engine_graph = engine._build_graph()

        junction_list = getattr(engine, "junctions", engine.wn.junction_name_list)
        for j in junction_list:
            if j == req.from_node_id or (target_nid and j == target_nid):
                continue
            j_lat, j_lng = latlngs.get(j, (0.0, 0.0))
            dist_cand = haversine_m(to_lat, to_lng, j_lat, j_lng)
            if dist_cand <= 120.0:
                deg = engine_graph.degree(j) if engine_graph is not None and j in engine_graph else 1
                loop_candidates.append(
                    {
                        "node_id": j,
                        "distance_m": round(dist_cand, 1),
                        "is_dead_end": bool(deg == 1),
                    }
                )
        loop_candidates.sort(key=lambda x: x["distance_m"])
        loop_candidates = loop_candidates[:5]

        if length_m > 500.0:
            warnings.append(
                f"Pipe segment length ({length_m:.1f} m) exceeds 500 m; consider intermediate junctions or air release valves."
            )
        if abs(slope_pct) > 10.0:
            warnings.append(
                f"Steep ground slope ({slope_pct:.1f}%); check for high static pressure or cavitation risk."
            )
        base_p = engine.baseline_pressure.get(req.from_node_id) if hasattr(engine, "baseline_pressure") else None
        if base_p is not None and base_p < REQUIRED_PRESSURE_M:
            warnings.append(
                f"Source node '{req.from_node_id}' baseline pressure is low ({base_p:.1f} m < {REQUIRED_PRESSURE_M} m required)."
            )

        return IntelliSenseResponse(
            network_revision=engine.network_revision,
            length_m=round(length_m, 2),
            elevation_from_m=elev_from_m,
            elevation_to_m=elev_to_m,
            elevation_diff_m=elev_diff_m,
            slope_pct=slope_pct,
            elevation_provenance=elevation_provenance,
            estimated_downstream_demand_lps=round(target_demand_lps, 3),
            estimated_households=est_households,
            recommended_diameter_mm=chosen_d_mm,
            target_velocity_ms=req.target_velocity_ms,
            predicted_velocity_ms=pred_v,
            predicted_headloss_m=pred_hf,
            loop_candidates=loop_candidates,
            warnings=warnings,
        )

    # ─────────────────────────── 5. Builder Preview & Commit ───────────────────────────
    def preview_build(
        self, engine: Any, req: NetworkBuildPreviewRequest
    ) -> NetworkBuildPreviewResponse:
        """
        Validate and simulate proposed network node and pipe additions.
        Evaluates topology, computes before/after hydraulic metrics, and returns preview token.
        """
        if req.expected_network_revision and req.expected_network_revision != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Stale network revision: expected '{req.expected_network_revision}', active is '{engine.network_revision}'.",
            )

        warnings: List[str] = []
        errors: List[str] = []

        existing_nodes = set(engine.wn.node_name_list)
        existing_links = set(engine.wn.link_name_list)

        seen_new_nodes = set()
        for n in req.new_nodes:
            if n.id in seen_new_nodes:
                errors.append(f"Duplicate node ID '{n.id}' within new_nodes.")
            if n.id in existing_nodes:
                errors.append(f"Node ID '{n.id}' already exists in active network.")
            seen_new_nodes.add(n.id)

        all_nodes = existing_nodes.union(seen_new_nodes)

        seen_new_pipes = set()
        for p in req.new_pipes:
            if p.id in seen_new_pipes:
                errors.append(f"Duplicate pipe ID '{p.id}' within new_pipes.")
            if p.id in existing_links:
                errors.append(f"Pipe ID '{p.id}' already exists in active network.")
            if p.from_node == p.to_node:
                errors.append(f"Pipe '{p.id}' forms a self-loop (from_node == to_node).")
            if p.from_node not in all_nodes:
                errors.append(f"Pipe '{p.id}' references unknown from_node '{p.from_node}'.")
            if p.to_node not in all_nodes:
                errors.append(f"Pipe '{p.id}' references unknown to_node '{p.to_node}'.")
            seen_new_pipes.add(p.id)

        if errors:
            return NetworkBuildPreviewResponse(
                valid=False,
                preview_token=None,
                source_network_revision=engine.network_revision,
                before_metrics=None,
                after_metrics=None,
                warnings=warnings,
                errors=errors,
            )

        wn_clone = _clone_wn(engine)
        meta_clone = copy.deepcopy(engine.meta) if isinstance(engine.meta, dict) else {}
        latlngs_clone = copy.deepcopy(getattr(engine, "latlngs", getattr(engine, "latlng", {})))

        if "nodes" not in meta_clone:
            meta_clone["nodes"] = {}
        if "pipes" not in meta_clone:
            meta_clone["pipes"] = {}
        if "households" not in meta_clone:
            meta_clone["households"] = {}
        if "road_class" not in meta_clone:
            meta_clone["road_class"] = {}

        for n in req.new_nodes:
            elev = n.elevation_m if n.elevation_m is not None else 800.0
            wn_clone.add_junction(
                n.id,
                base_demand=n.base_demand_lps / 1000.0,
                elevation=elev,
                coordinates=(n.lng, n.lat),
            )
            latlngs_clone[n.id] = (n.lat, n.lng)
            base_lpd = n.base_demand_lps * 86400.0
            hh = int(min(max(round(base_lpd / (LPCD * PERSONS_PER_HOUSEHOLD)), 1), 500)) if n.base_demand_lps > 0 else 1
            meta_clone["nodes"][n.id] = {
                "elevation_m": elev,
                "base_demand_lps": n.base_demand_lps,
                "households": hh,
            }
            meta_clone["households"][n.id] = hh

        for p in req.new_pipes:
            pt1 = latlngs_clone.get(p.from_node, (12.9081, 77.4852))
            pt2 = latlngs_clone.get(p.to_node, (12.9081, 77.4852))
            len_m = max(5.0, haversine_m(pt1[0], pt1[1], pt2[0], pt2[1]))
            if len_m > 500.0:
                warnings.append(f"Proposed pipe '{p.id}' length ({len_m:.1f} m) exceeds 500 m.")
            wn_clone.add_pipe(
                p.id,
                p.from_node,
                p.to_node,
                length=len_m,
                diameter=p.diameter_mm / 1000.0,
                roughness=p.roughness,
                minor_loss=0.0,
            )
            meta_clone["pipes"][p.id] = {
                "road_class": p.road_class,
                "diameter_mm": p.diameter_mm,
                "length_m": round(len_m, 2),
            }
            meta_clone["road_class"][p.id] = p.road_class

        wn_clone.options.time.duration = 0
        wn_clone.options.hydraulic.demand_model = "PDD"
        wn_clone.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
        wn_clone.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
        wn_clone.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

        try:
            sim = wntr.sim.WNTRSimulator(wn_clone)
            sim_res = sim.run_sim()
            after_pressures_raw = sim_res.node["pressure"].iloc[0].to_dict()
            after_pressures = {k: float(v) for k, v in after_pressures_raw.items() if k in wn_clone.junction_name_list}
        except Exception as sim_err:
            return NetworkBuildPreviewResponse(
                valid=False,
                preview_token=None,
                source_network_revision=engine.network_revision,
                before_metrics=None,
                after_metrics=None,
                warnings=warnings,
                errors=[f"Hydraulic solver failed on proposed network extension: {sim_err}"],
            )

        before_pressures = getattr(engine, "baseline_pressure", {})
        before_hh = getattr(engine, "households", meta_clone.get("households", {}))
        before_metrics = _compute_metrics(before_pressures, before_hh)
        after_metrics = _compute_metrics(after_pressures, meta_clone["households"])

        if after_metrics.pressure_deficit_nodes > before_metrics.pressure_deficit_nodes:
            warnings.append(
                f"Proposed extension increases deficit nodes from {before_metrics.pressure_deficit_nodes} to {after_metrics.pressure_deficit_nodes}."
            )

        token = f"bld-{uuid4().hex[:12]}"
        st_type = getattr(engine, "source_type", "synthetic")

        with self._lock:
            self._prune_cache()
            self._preview_cache[token] = {
                "type": "build",
                "wn": wn_clone,
                "meta": meta_clone,
                "source_type": st_type,
                "source_network_revision": engine.network_revision,
                "created_at": time.time(),
            }

        return NetworkBuildPreviewResponse(
            valid=True,
            preview_token=token,
            source_network_revision=engine.network_revision,
            before_metrics=before_metrics,
            after_metrics=after_metrics,
            warnings=warnings,
            errors=[],
        )

    def commit_build(
        self, engine: Any, req: NetworkBuildCommitRequest
    ) -> NetworkRevisionCommitResponse:
        """Commit a network extension previously validated via preview_build."""
        if not req.confirm:
            raise HTTPException(status_code=422, detail="Build commit requires confirm=True.")

        with self._lock:
            prev = self._preview_cache.get(req.preview_token)

        if not prev or prev.get("type") != "build":
            raise HTTPException(status_code=404, detail=f"Preview token '{req.preview_token}' not found or expired.")

        if req.expected_network_revision != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Stale network revision: expected '{req.expected_network_revision}', active is '{engine.network_revision}'.",
            )
        if prev["source_network_revision"] != engine.network_revision:
            raise HTTPException(
                status_code=409,
                detail=f"Revision conflict: preview was created against '{prev['source_network_revision']}', active is '{engine.network_revision}'.",
            )

        prev_rev = engine.network_revision
        meta_dict: Dict[str, Any]

        if hasattr(engine, "promote_network"):
            meta_dict = engine.promote_network(
                prev["wn"],
                meta=prev["meta"],
                source_type=prev["source_type"],
                source_filename=getattr(engine, "source_filename", None),
                network_name=engine.network_id,
                rebuild_matrix=True,
            )
        else:
            meta_dict = engine.activate_network(
                prev["wn"],
                meta=prev["meta"],
                source_type=prev["source_type"],
                source_filename=getattr(engine, "source_filename", None),
                network_name=engine.network_id,
            )

        with self._lock:
            self._preview_cache.pop(req.preview_token, None)

        rev_meta = _build_revision_meta(engine, meta_dict)

        return NetworkRevisionCommitResponse(
            committed=True,
            previous_network_revision=prev_rev,
            new_network_revision=engine.network_revision,
            revision_meta=rev_meta,
            message=f"Successfully committed network build extension to revision '{engine.network_revision}'.",
        )

    # ─────────────────────────── 6. Network Reset / Rollback ───────────────────────────
    def reset_network(
        self, engine: Any, req: NetworkResetRequest
    ) -> NetworkRevisionCommitResponse:
        """Rollback network to 'previous' revision or initial 'baseline' state."""
        if not req.confirm:
            raise HTTPException(status_code=422, detail="Network reset requires confirm=True.")

        prev_rev = engine.network_revision
        try:
            meta_dict = engine.rollback_network(target=req.target)
        except ValueError as val_err:
            raise HTTPException(status_code=422, detail=str(val_err))
        except Exception as err:
            raise HTTPException(status_code=422, detail=f"Rollback failed: {err}")

        rev_meta = _build_revision_meta(engine, meta_dict)

        return NetworkRevisionCommitResponse(
            committed=True,
            previous_network_revision=prev_rev,
            new_network_revision=engine.network_revision,
            revision_meta=rev_meta,
            message=f"Successfully rolled back network to {req.target} revision ({engine.network_revision}).",
        )


_default_manager = NetworkBuilderManager()


def list_network_presets(engine: Any) -> NetworkPresetsListResponse:
    return _default_manager.list_network_presets(engine)


def preview_import(engine: Any, req: NetworkImportPreviewRequest) -> NetworkImportPreviewResponse:
    return _default_manager.preview_import(engine, req)


def commit_import(engine: Any, req: NetworkImportCommitRequest) -> NetworkRevisionCommitResponse:
    return _default_manager.commit_import(engine, req)


def compute_intellisense(engine: Any, req: IntelliSenseRequest) -> IntelliSenseResponse:
    return _default_manager.compute_intellisense(engine, req)


def preview_build(engine: Any, req: NetworkBuildPreviewRequest) -> NetworkBuildPreviewResponse:
    return _default_manager.preview_build(engine, req)


def commit_build(engine: Any, req: NetworkBuildCommitRequest) -> NetworkRevisionCommitResponse:
    return _default_manager.commit_build(engine, req)


def reset_network(engine: Any, req: NetworkResetRequest) -> NetworkRevisionCommitResponse:
    return _default_manager.reset_network(engine, req)
