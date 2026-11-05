Exit code: 0
Wall time: 3.5 seconds
Output:
"""
Geographic coordinate mapping and spatial utilities for HydroPulse.
Handles conversion of arbitrary EPANET / WNTR (x,y) networks into
real Bengaluru / Kengeri coordinates.
"""

import math
from typing import Dict, Tuple, Any

KENGERI_BBOX = dict(
    lat_min=12.9000,
    lat_max=12.9160,
    lng_min=77.4760,
    lng_max=77.4940,
)

BENGALURU_BOUNDS = dict(
    lat_min=12.70,
    lat_max=13.30,
    lng_min=77.30,
    lng_max=77.90,
)


def is_geographic(coords: Dict[str, Tuple[float, float]]) -> bool:
    """
    Check if all coordinates (x=lng, y=lat) fall within BENGALURU_BOUNDS.
    Returns True only if coords is non-empty and every point is within bounds.
    """
    if not coords:
        return False

    for pt in coords.values():
        if pt is None or len(pt) < 2:
            return False
        x, y = pt[0], pt[1]  # x = lng, y = lat
        if not (BENGALURU_BOUNDS["lng_min"] <= x <= BENGALURU_BOUNDS["lng_max"]):
            return False
        if not (BENGALURU_BOUNDS["lat_min"] <= y <= BENGALURU_BOUNDS["lat_max"]):
            return False

    return True


def normalize_to_bbox(
    coords: Dict[str, Tuple[float, float]],
    bbox: Dict[str, float] = KENGERI_BBOX,
) -> Dict[str, Tuple[float, float]]:
    """
    Linearly map arbitrary (x, y) coordinates into bbox, keeping aspect ratio
    and centering. Returns dict mapping node_id -> (lat, lng).
    """
    if not coords:
        return {}

    valid_pts = {k: v for k, v in coords.items() if v is not None}
    if not valid_pts:
        return {}

    xs = [pt[0] for pt in valid_pts.values()]
    ys = [pt[1] for pt in valid_pts.values()]

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    dx = x_max - x_min
    dy = y_max - y_min

    bbox_lat_span = bbox["lat_max"] - bbox["lat_min"]
    bbox_lng_span = bbox["lng_max"] - bbox["lng_min"]
    center_lat = (bbox["lat_min"] + bbox["lat_max"]) / 2.0
    center_lng = (bbox["lng_min"] + bbox["lng_max"]) / 2.0

    # Handle degenerate case: single point or zero range
    if dx == 0 and dy == 0:
        return {k: (round(center_lat, 6), round(center_lng, 6)) for k in coords}

    if dx == 0:
        scale = bbox_lat_span / dy
        span_y = dy * scale
        offset_lat = bbox["lat_min"] + (bbox_lat_span - span_y) / 2.0
        result = {}
        for k, pt in coords.items():
            if pt is None:
                result[k] = (round(center_lat, 6), round(center_lng, 6))
            else:
                lat = offset_lat + (pt[1] - y_min) * scale
                result[k] = (round(lat, 6), round(center_lng, 6))
        return result

    if dy == 0:
        scale = bbox_lng_span / dx
        span_x = dx * scale
        offset_lng = bbox["lng_min"] + (bbox_lng_span - span_x) / 2.0
        result = {}
        for k, pt in coords.items():
            if pt is None:
                result[k] = (round(center_lat, 6), round(center_lng, 6))
            else:
                lng = offset_lng + (pt[0] - x_min) * scale
                result[k] = (round(center_lat, 6), round(lng, 6))
        return result

    # Aspect-preserving scale
    scale = min(bbox_lng_span / dx, bbox_lat_span / dy)
    span_x = dx * scale
    span_y = dy * scale

    offset_lng = bbox["lng_min"] + (bbox_lng_span - span_x) / 2.0
    offset_lat = bbox["lat_min"] + (bbox_lat_span - span_y) / 2.0

    result = {}
    for k, pt in coords.items():
        if pt is None:
            result[k] = (round(center_lat, 6), round(center_lng, 6))
        else:
            lng = offset_lng + (pt[0] - x_min) * scale
            lat = offset_lat + (pt[1] - y_min) * scale
            result[k] = (round(lat, 6), round(lng, 6))

    return result


def map_network_coordinates(wn: Any) -> Dict[str, Tuple[float, float]]:
    """
    Read coordinates from a WNTR WaterNetworkModel.
    Passes through if coordinates are already geographic (in Bengaluru),
    otherwise normalizes them into KENGERI_BBOX.
    Missing node coordinates are placed at neighbor centroid.
    Returns: dict of node_id -> (lat, lng).
    """
    raw_coords: Dict[str, Tuple[float, float]] = {}
    missing_nodes = []

    for name in wn.node_name_list:
        node = wn.get_node(name)
        coords = getattr(node, "coordinates", None)
        if coords is not None and len(coords) >= 2 and (coords[0] != 0.0 or coords[1] != 0.0):
            raw_coords[name] = (float(coords[0]), float(coords[1]))
        else:
            missing_nodes.append(name)

    # Fill missing nodes from graph neighbors if possible
    if missing_nodes:
        graph = getattr(wn, "graph", None)
        if graph is None:
            try:
                import wntr.network
                graph = wntr.network.to_graph(wn)
            except Exception:
                graph = None

        for name in missing_nodes:
            if graph is not None and name in graph:
                neighbors = list(graph.neighbors(name))
                known_nbr_coords = [raw_coords[nbr] for nbr in neighbors if nbr in raw_coords]
                if known_nbr_coords:
                    avg_x = sum(c[0] for c in known_nbr_coords) / len(known_nbr_coords)
                    avg_y = sum(c[1] for c in known_nbr_coords) / len(known_nbr_coords)
                    raw_coords[name] = (avg_x, avg_y)
                    continue

            # Fallback to mean of all known coords or origin
            if raw_coords:
                avg_x = sum(c[0] for c in raw_coords.values()) / len(raw_coords)
                avg_y = sum(c[1] for c in raw_coords.values()) / len(raw_coords)
                raw_coords[name] = (avg_x, avg_y)
            else:
                raw_coords[name] = (77.4852, 12.9081)

    if is_geographic(raw_coords):
        # Already geographic: x = lng, y = lat -> return (lat, lng)
        return {k: (round(v[1], 6), round(v[0], 6)) for k, v in raw_coords.items()}

    # Otherwise normalize arbitrary planar coords to KENGERI_BBOX
    return normalize_to_bbox(raw_coords, bbox=KENGERI_BBOX)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate great-circle distance in meters between two lat/lng coordinates.
    """
    if lat1 == lat2 and lng1 == lng2:
        return 0.0

    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))  # Guard domain errors
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(R * c, 2)


def network_center(latlngs: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
    """
    Compute centroid (lat, lng) for a dict of node_id -> (lat, lng).
    """
    if not latlngs:
        return (12.9081, 77.4852)

    lats = [pt[0] for pt in latlngs.values()]
    lngs = [pt[1] for pt in latlngs.values()]
    return (round(sum(lats) / len(lats), 6), round(sum(lngs) / len(lngs), 6))


if __name__ == "__main__":
    # Self-checks
    geo_test = {"J1": (77.4852, 12.9081), "J2": (77.4860, 12.9090)}
    assert is_geographic(geo_test) is True, "Failed is_geographic check"

    planar_test = {"10": (100.0, 200.0), "20": (500.0, 800.0)}
    assert is_geographic(planar_test) is False, "Failed planar is_geographic check"

    normed = normalize_to_bbox(planar_test)
    assert len(normed) == 2, "normalize_to_bbox should return 2 points"
    assert KENGERI_BBOX["lat_min"] <= normed["10"][0] <= KENGERI_BBOX["lat_max"]
    assert KENGERI_BBOX["lng_min"] <= normed["10"][1] <= KENGERI_BBOX["lng_max"]

    dist_same = haversine_m(12.9081, 77.4852, 12.9081, 77.4852)
    assert dist_same == 0.0, f"Expected 0.0, got {dist_same}"

    dist = haversine_m(12.9081, 77.4852, 12.9081, 77.4862)
    assert 100 < dist < 120, f"Expected ~108m, got {dist}"

    center = network_center(normed)
    assert KENGERI_BBOX["lat_min"] <= center[0] <= KENGERI_BBOX["lat_max"]

    print("All geo_mapper self-checks passed!")

