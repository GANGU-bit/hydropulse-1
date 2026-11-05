Exit code: 0
Wall time: 4.6 seconds
Output:
"""Offline contract checks for the frontend mock fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import (
    HealthResponse,
    LeakSimulationResponse,
    NetworkResponse,
    PresetsResponse,
)

MOCK_DIR = REPO_ROOT / "frontend" / "public" / "mock"


def _load(name: str) -> dict:
    with (MOCK_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def test_mock_fixtures_match_the_contract() -> None:
    """Every committed fixture validates without requiring a running backend."""
    health = HealthResponse.model_validate(_load("health.json"))
    network = NetworkResponse.model_validate(_load("network.json"))
    presets = PresetsResponse.model_validate(_load("presets.json"))

    assert health.num_junctions == sum(
        node.type == "junction" for node in network.nodes
    )
    assert health.num_pipes == len(network.pipes)
    assert health.sensor_ids == network.sensor_ids
    assert len(presets.presets) == 3

    junction_ids = {node.id for node in network.nodes if node.type == "junction"}
    for preset in presets.presets:
        assert preset.target_node_id in junction_ids
        response = LeakSimulationResponse.model_validate(
            _load(f"simulate_leak_{preset.id}.json")
        )
        assert response.injected_node_id == preset.target_node_id
        assert set(response.node_heatmap) == junction_ids
        assert len(response.sensor_readings) == 5

    default_response = LeakSimulationResponse.model_validate(
        _load("simulate_leak_default.json")
    )
    assert set(default_response.node_heatmap) == junction_ids

