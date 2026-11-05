"""Unit tests for Gangu-owned data utility modules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.build_presets import build_presets
from data_utils.geo_mapper import (
    KENGERI_BBOX,
    haversine_m,
    is_geographic,
    network_center,
    normalize_to_bbox,
)
from data_utils.schemas import PresetsResponse


def test_coordinate_utilities_preserve_coordinate_conventions() -> None:
    """Geographic coordinates use WNTR's (lng, lat) ordering."""
    geographic = {"J1": (77.4852, 12.9081)}
    assert is_geographic(geographic)

    normalized = normalize_to_bbox({"J1": (0.0, 0.0), "J2": (10.0, 20.0)})
    for lat, lng in normalized.values():
        assert KENGERI_BBOX["lat_min"] <= lat <= KENGERI_BBOX["lat_max"]
        assert KENGERI_BBOX["lng_min"] <= lng <= KENGERI_BBOX["lng_max"]

    assert haversine_m(12.9081, 77.4852, 12.9081, 77.4852) == 0.0
    assert network_center(normalized) != (0.0, 0.0)


def test_build_presets_requires_a_committed_network(tmp_path: Path) -> None:
    """Synthetic fixture IDs must never be copied into real presets."""
    with pytest.raises(FileNotFoundError, match="Committed network is required"):
        build_presets(
            inp_path=str(tmp_path / "missing.inp"),
            meta_path=str(tmp_path / "missing-meta.json"),
            out_path=str(tmp_path / "presets.json"),
        )


def test_build_presets_selects_three_real_junctions(tmp_path: Path) -> None:
    """Preset selection writes only junction IDs from the supplied EPANET model."""
    wntr = pytest.importorskip("wntr")
    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("R1", base_head=850.0, coordinates=(77.4852, 12.9081))
    for number in range(1, 8):
        model.add_junction(
            f"J{number}",
            base_demand=0.001,
            demand_pattern=None,
            elevation=810.0,
            coordinates=(77.4852 + number * 0.0001, 12.9081),
        )

    model.add_pipe("P0", "R1", "J1", length=100.0, diameter=0.4, roughness=100)
    for number in range(1, 7):
        model.add_pipe(
            f"P{number}",
            f"J{number}",
            f"J{number + 1}",
            length=100.0,
            diameter=0.25 if number < 6 else 0.11,
            roughness=100,
        )

    inp_path = tmp_path / "network.inp"
    meta_path = tmp_path / "network_meta.json"
    output_path = tmp_path / "presets.json"
    wntr.network.write_inpfile(model, str(inp_path))
    meta_path.write_text(json.dumps({"sensor_ids": ["J2", "J4", "J6"]}), encoding="utf-8")

    build_presets(str(inp_path), str(meta_path), str(output_path))
    response = PresetsResponse.model_validate_json(output_path.read_text(encoding="utf-8"))

    assert len(response.presets) == 3
    assert {preset.target_node_id for preset in response.presets}.issubset(
        set(model.junction_name_list)
    )

