"""Coordinate helpers: WNTR (x=lng, y=lat) → (lat, lng).

TEMPORARY STAND-IN written by the backend lead. Owner: Gangu. The public
interface (names and signatures) follows 02_gangu_plan.md TASK 3.
"""

from __future__ import annotations

import math
from typing import Iterable

KENGERI_BBOX = dict(lat_min=12.9000, lat_max=12.9160, lng_min=77.4760, lng_max=77.4940)
BENGALURU_BOUNDS = dict(lat_min=12.70, lat_max=13.30, lng_min=77.30, lng_max=77.90)


def is_geographic(coords: dict[str, tuple[float, float]]) -> bool:
    """True if every (x=lng, y=lat) pair lies inside BENGALURU_BOUNDS."""
    b = BENGALURU_BOUNDS
    return bool(coords) and all(
        b["lng_min"] <= x <= b["lng_max"] and b["lat_min"] <= y <= b["lat_max"]
        for x, y in coords.values()
    )


def normalize_to_bbox(
    coords: dict[str, tuple[float, float]], bbox: dict = KENGERI_BBOX
) -> dict[str, tuple[float, float]]:
    """Linearly map arbitrary (x, y) into bbox, keeping aspect ratio, centered.

    Returns {id: (lat, lng)}.
    """
    if not coords:
        return {}
    xs = [c[0] for c in coords.values()]
    ys = [c[1] for c in coords.values()]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    xr, yr = xmax - xmin, ymax - ymin
    lat_span = bbox["lat_max"] - bbox["lat_min"]
    lng_span = bbox["lng_max"] - bbox["lng_min"]
    lat_c = (bbox["lat_max"] + bbox["lat_min"]) / 2
    lng_c = (bbox["lng_max"] + bbox["lng_min"]) / 2
    if xr == 0 and yr == 0:
        return {k: (lat_c, lng_c) for k in coords}
    scale = min(lng_span / xr if xr else math.inf, lat_span / yr if yr else math.inf)
    xc, yc = (xmax + xmin) / 2, (ymax + ymin) / 2
    return {
        k: (lat_c + (y - yc) * scale, lng_c + (x - xc) * scale)
        for k, (x, y) in coords.items()
    }


def map_network_coordinates(wn) -> dict[str, tuple[float, float]]:
    """{node_id: (lat, lng)} for every node of a WNTR model.

    Real Bengaluru coordinates pass through; anything else (e.g. Net3) is
    normalised into the Kengeri bbox. Nodes without coordinates are placed at
    the centroid of their neighbours.
    """
    raw: dict[str, tuple[float, float]] = {}
    missing: list[str] = []
    for name in wn.node_name_list:
        c = wn.get_node(name).coordinates
        if c is None:
            missing.append(name)
        else:
            raw[name] = (float(c[0]), float(c[1]))
    if is_geographic(raw):
        out = {k: (y, x) for k, (x, y) in raw.items()}
    else:
        out = normalize_to_bbox(raw)
    if missing:
        g = wn.to_graph().to_undirected()
        for name in missing:
            nbrs = [out[n] for n in g.neighbors(name) if n in out]
            out[name] = network_center(nbrs) if nbrs else network_center(out.values())
    return out


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def network_center(latlngs: Iterable[tuple[float, float]]) -> tuple[float, float]:
    """Arithmetic mean (lat, lng)."""
    pts = list(latlngs)
    if not pts:
        return (
            (KENGERI_BBOX["lat_min"] + KENGERI_BBOX["lat_max"]) / 2,
            (KENGERI_BBOX["lng_min"] + KENGERI_BBOX["lng_max"]) / 2,
        )
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


if __name__ == "__main__":
    assert haversine_m(12.9, 77.48, 12.9, 77.48) == 0.0
    d = haversine_m(12.9, 77.48, 12.901, 77.48)
    assert 110 < d < 112, d
    m = normalize_to_bbox({"a": (0, 0), "b": (100, 50)})
    for lat, lng in m.values():
        assert KENGERI_BBOX["lat_min"] <= lat <= KENGERI_BBOX["lat_max"]
        assert KENGERI_BBOX["lng_min"] <= lng <= KENGERI_BBOX["lng_max"]
    assert is_geographic({"a": (77.48, 12.9)}) and not is_geographic({"a": (0, 0)})
    print("geo_mapper self-checks passed")
