"""OpenStreetMap streets → EPANET pipe network for a Kengeri DMA.

Usage (from the repo root):
    python backend/generate_bangalore_dma.py                     # real OSM download (needs internet)
    python backend/generate_bangalore_dma.py --source synthetic  # offline, street-like grid in the Kengeri bbox

Both sources produce the same intermediate graph (nodes with x=lng, y=lat; edges
with `length` and `highway`) and then run through one shared pipeline:
rename → elevation/households proxies → pipe sizing → reservoir → pressure
validation → write .inp + network_meta.json.

Modelling proxies (documented, not survey data):
- Elevation = 815 m + a gentle planar slope (~15 m across the bbox) + N(0, 0.5) noise.
- Households per junction = uniform 10–80; demand = households × 4.5 persons × 135 LPCD.
- Pipe diameter follows the road class (trunk/primary 400 mm, secondary/tertiary 250 mm, else 110 mm).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
import wntr

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.geo_mapper import haversine_m  # noqa: E402

LPCD = 135.0
PERSONS_PER_HOUSEHOLD = 4.5
MIN_JUNCTIONS, MAX_JUNCTIONS = 60, 250
MIN_P, MAX_P = 15.0, 70.0


# ───────────────────────────── graph sources ─────────────────────────────
def _clean_graph(g: nx.Graph) -> nx.Graph:
    """Largest connected component, no self-loops, parallel edges collapsed to the shortest."""
    out = nx.Graph()
    for n, d in g.nodes(data=True):
        out.add_node(n, x=float(d["x"]), y=float(d["y"]))
    for u, v, d in g.edges(data=True):
        if u == v:
            continue
        length = float(d.get("length", 1.0))
        hw = d.get("highway", "residential")
        if isinstance(hw, (list, tuple)):
            hw = hw[0]
        if out.has_edge(u, v) and out[u][v]["length"] <= length:
            continue
        out.add_edge(u, v, length=length, highway=str(hw))
    largest = max(nx.connected_components(out), key=len)
    return out.subgraph(largest).copy()


def osm_graph(lat: float, lng: float, dist: float) -> nx.Graph:
    """Download the OSM drive network and return a cleaned undirected graph."""
    import osmnx as ox

    gm = ox.graph_from_point((lat, lng), dist=dist, network_type="drive")
    if hasattr(ox, "convert") and hasattr(ox.convert, "to_undirected"):
        gu = ox.convert.to_undirected(gm)  # osmnx v2
    else:
        gu = ox.get_undirected(gm)  # osmnx v1
    return _clean_graph(nx.Graph(gu) if not gu.is_multigraph() else _multi_to_simple(gu))


def _multi_to_simple(gm) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(gm.nodes(data=True))
    for u, v, d in gm.edges(data=True):
        if g.has_edge(u, v) and g[u][v].get("length", math.inf) <= d.get("length", math.inf):
            continue
        g.add_edge(u, v, **d)
    return g


def synthetic_graph(lat: float, lng: float, dist: float, seed: int) -> nx.Graph:
    """Offline street-like network centred on (lat, lng).

    A jittered grid (~110 m blocks) with two primary arterials, two secondary
    roads, ~12% of residential links removed (connectivity kept) and a few
    dead-end lanes. IDs are integers standing in for OSM ids.
    """
    rng = np.random.default_rng(seed)
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(lat))
    spacing = 110.0
    k = int(round(2 * dist / spacing)) // 2 * 2  # even → odd number of lines
    rows = cols = k + 1 if k + 1 <= 11 else 11
    g = nx.Graph()
    nid = lambda r, c: 1_000_000 + r * 100 + c  # noqa: E731
    for r in range(rows):
        for c in range(cols):
            dx = (c - (cols - 1) / 2) * spacing + rng.normal(0, 18)
            dy = (r - (rows - 1) / 2) * spacing + rng.normal(0, 18)
            g.add_node(nid(r, c), x=lng + dx / m_per_deg_lng, y=lat + dy / m_per_deg_lat)
    mid = (rows - 1) // 2
    primary_rows, primary_cols = {mid}, {mid}
    secondary_rows, secondary_cols = {1, rows - 2}, {2}

    def hw(r1, c1, r2, c2) -> str:
        if r1 == r2 and r1 in primary_rows or c1 == c2 and c1 in primary_cols:
            return "primary"
        if r1 == r2 and r1 in secondary_rows or c1 == c2 and c1 in secondary_cols:
            return "secondary"
        return "residential"

    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                g.add_edge(nid(r, c), nid(r, c + 1), highway=hw(r, c, r, c + 1))
            if r + 1 < rows:
                g.add_edge(nid(r, c), nid(r + 1, c), highway=hw(r, c, r + 1, c))
    # Remove some residential links while staying connected (irregular blocks).
    residential = [e for e in g.edges if g.edges[e]["highway"] == "residential"]
    rng.shuffle(residential)
    removed, target_removed = 0, int(0.12 * g.number_of_edges())
    for u, v in residential:
        if removed >= target_removed:
            break
        g.remove_edge(u, v)
        if g.degree(u) < 2 or g.degree(v) < 2 or not nx.is_connected(g):
            g.add_edge(u, v, highway="residential")
            continue
        removed += 1
    # Dead-end lanes off random residential nodes.
    candidates = [n for n in g.nodes if g.degree(n) <= 3]
    for i, base in enumerate(rng.choice(candidates, size=min(8, len(candidates)), replace=False)):
        bx, by = g.nodes[base]["x"], g.nodes[base]["y"]
        ang = rng.uniform(0, 2 * math.pi)
        dlen = rng.uniform(45, 80)
        new = 2_000_000 + i
        g.add_node(
            new,
            x=bx + dlen * math.cos(ang) / m_per_deg_lng,
            y=by + dlen * math.sin(ang) / m_per_deg_lat,
        )
        g.add_edge(int(base), new, highway="residential")
    for u, v in g.edges:
        a, b = g.nodes[u], g.nodes[v]
        g.edges[u, v]["length"] = 1.05 * haversine_m(a["y"], a["x"], b["y"], b["x"])
    return _clean_graph(g)


# ───────────────────────────── network build ─────────────────────────────
def diameter_for(highway: str) -> float:
    """Pipe diameter (m) from the OSM highway class."""
    h = highway.replace("_link", "")
    if h in ("trunk", "primary"):
        return 0.40
    if h in ("secondary", "tertiary"):
        return 0.25
    return 0.11


def road_class_for(highway: str) -> str:
    """Normalised road_class enum (SOURCE_OF_TRUTH §5)."""
    h = highway.replace("_link", "")
    if h in ("trunk", "primary"):
        return "primary"
    if h in ("secondary", "tertiary"):
        return "secondary"
    return "residential"


def build_network(g: nx.Graph, seed: int) -> tuple[wntr.network.WaterNetworkModel, dict]:
    """Turn a cleaned street graph into a WNTR model plus metadata."""
    rng = np.random.default_rng(seed)
    order = sorted(g.nodes, key=lambda n: int(n) if str(n).isdigit() else str(n))
    jname = {n: f"J{i + 1}" for i, n in enumerate(order)}
    xs = np.array([g.nodes[n]["x"] for n in order])
    ys = np.array([g.nodes[n]["y"] for n in order])
    # Planar slope: ~15 m drop from north-east to south-west across the bbox.
    span = max(np.ptp(xs) + np.ptp(ys), 1e-9)
    slope = 15.0 * (((xs - xs.min()) + (ys - ys.min())) / span)
    elev = 815.0 + slope + rng.normal(0, 0.5, size=len(order))

    wn = wntr.network.WaterNetworkModel()
    households: dict[str, int] = {}
    osmid: dict[str, int] = {}
    for i, n in enumerate(order):
        name = jname[n]
        hh = int(rng.integers(10, 81))
        households[name] = hh
        osmid[name] = int(n) if str(n).isdigit() else -1
        demand_m3s = hh * PERSONS_PER_HOUSEHOLD * LPCD / 1000.0 / 86400.0
        wn.add_junction(
            name,
            base_demand=demand_m3s,
            elevation=float(elev[i]),
            coordinates=(float(xs[i]), float(ys[i])),
        )

    edges = sorted(
        g.edges(data=True),
        key=lambda e: tuple(sorted((int(jname[e[0]][1:]), int(jname[e[1]][1:])))),
    )
    road_class: dict[str, str] = {}
    for k, (u, v, d) in enumerate(edges, start=1):
        a, b = sorted((jname[u], jname[v]), key=lambda s: int(s[1:]))
        pname = f"P{k}"
        wn.add_pipe(
            pname, a, b,
            length=max(float(d["length"]), 1.0),
            diameter=diameter_for(d["highway"]),
            roughness=120.0,
        )
        road_class[pname] = road_class_for(d["highway"])

    # Reservoir ~50 m off the highest junction that sits on a primary main (falls back
    # to the highest junction overall). Feeding a trunk, not a 110 mm lane, mirrors how
    # a DMA inlet works and avoids one lane becoming the bottleneck for the whole ward.
    on_primary = {jname[n] for u, v, d in g.edges(data=True) if road_class_for(d["highway"]) == "primary"
                  for n in (u, v)}
    idx = [i for i, n in enumerate(order) if jname[n] in on_primary] or list(range(len(order)))
    top = max(idx, key=lambda i: elev[i])
    tx, ty = xs[top], ys[top]
    m_lng = 111_320.0 * math.cos(math.radians(ty))
    wn.add_reservoir(
        "R1",
        base_head=float(elev.max() + 45.0),
        coordinates=(float(tx + 35.0 / m_lng), float(ty + 35.0 / 111_320.0)),
    )
    wn.add_pipe("P0", "R1", jname[order[top]], length=50.0, diameter=0.50, roughness=120.0)
    road_class["P0"] = "feeder"
    apply_options(wn)
    meta = {"households": households, "road_class": road_class, "osmid": osmid}
    return wn, meta


def apply_options(wn: wntr.network.WaterNetworkModel) -> None:
    """Steady-state PDD invariants (SOURCE_OF_TRUTH §6)."""
    wn.options.time.duration = 0
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.hydraulic.required_pressure = 15.0
    wn.options.hydraulic.minimum_pressure = 2.0
    wn.options.hydraulic.pressure_exponent = 0.5


def junction_pressures(wn: wntr.network.WaterNetworkModel):
    res = wntr.sim.WNTRSimulator(wn).run_sim()
    return res.node["pressure"].iloc[0][wn.junction_name_list], res


def validate_pressures(wn: wntr.network.WaterNetworkModel, max_iter: int = 30) -> None:
    """Raise reservoir head / upsize small pipes until 15 m ≤ pressure ≤ 70 m."""
    res_node = wn.get_node("R1")
    upsized = False
    for _ in range(max_iter):
        p, _ = junction_pressures(wn)
        lo, hi = float(p.min()), float(p.max())
        if lo >= MIN_P and hi <= MAX_P:
            return
        if lo < MIN_P and hi + 5.0 <= MAX_P:
            res_node.head_timeseries.base_value += 5.0
        elif lo < MIN_P and not upsized:
            for pn in wn.pipe_name_list:
                pipe = wn.get_link(pn)
                if abs(pipe.diameter - 0.11) < 1e-9:
                    pipe.diameter = 0.15
            upsized = True
        elif hi > MAX_P and lo - 5.0 >= MIN_P:
            res_node.head_timeseries.base_value -= 5.0
        else:
            break
    p, _ = junction_pressures(wn)
    raise RuntimeError(
        f"Could not bring pressures into [{MIN_P}, {MAX_P}] m: min={p.min():.1f}, max={p.max():.1f}"
    )


def write_inp(wn: wntr.network.WaterNetworkModel, path: Path) -> None:
    if hasattr(wntr.network, "write_inpfile"):
        wntr.network.write_inpfile(wn, str(path))
    else:
        wn.write_inpfile(str(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=12.9081)
    ap.add_argument("--lng", type=float, default=77.4852)
    ap.add_argument("--dist", type=float, default=600.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="backend/bangalore_kengeri.inp")
    ap.add_argument("--source", choices=["osm", "synthetic"], default="osm",
                    help="osm = download real streets (default); synthetic = offline street-like grid")
    args = ap.parse_args(argv)

    dist = args.dist
    for attempt in range(4):
        g = osm_graph(args.lat, args.lng, dist) if args.source == "osm" else synthetic_graph(
            args.lat, args.lng, dist, args.seed)
        n = g.number_of_nodes()
        print(f"[attempt {attempt + 1}] source={args.source} dist={dist:.0f} m → {n} intersections")
        if MIN_JUNCTIONS <= n <= MAX_JUNCTIONS:
            break
        dist = dist * (1.3 if n < MIN_JUNCTIONS else 0.75)
    else:
        print(f"ERROR: could not reach {MIN_JUNCTIONS}-{MAX_JUNCTIONS} junctions", file=sys.stderr)
        return 1

    wn, meta = build_network(g, args.seed)
    validate_pressures(wn)
    p, res = junction_pressures(wn)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_inp(wn, out)

    network_name = "bangalore_kengeri" if args.source == "osm" else "kengeri_synthetic"
    meta_doc = {
        "network_name": network_name,
        "center": {"lat": args.lat, "lng": args.lng},
        "households": meta["households"],
        "road_class": meta["road_class"],
        "osmid": meta["osmid"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {
            "source": args.source, "lat": args.lat, "lng": args.lng, "dist": dist,
            "seed": args.seed, "reservoir_head_m": round(wn.get_node("R1").base_head, 2),
            "elevation": "proxy: 815 m + ~15 m planar slope + N(0, 0.5) noise",
        },
    }
    (out.parent / "network_meta.json").write_text(json.dumps(meta_doc, indent=2) + "\n")

    total_demand_lps = sum(wn.get_node(j).base_demand for j in wn.junction_name_list) * 1000
    print(f"wrote {out} and {out.parent / 'network_meta.json'}")
    print(f"network_name={network_name} junctions={len(wn.junction_name_list)} "
          f"pipes={len(wn.pipe_name_list)} reservoirs={len(wn.reservoir_name_list)}")
    print(f"pressure m: min={p.min():.1f} mean={p.mean():.1f} max={p.max():.1f}")
    print(f"total demand: {total_demand_lps:.2f} L/s; households: {sum(meta['households'].values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
