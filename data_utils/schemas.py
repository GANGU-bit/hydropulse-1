"""Pydantic v2 mirror of API CONTRACT v1.0 (SOURCE_OF_TRUTH.md §7).

TEMPORARY STAND-IN written by the backend lead so the API could be built and
tested. Owner: Gangu. Replace freely as long as every field name, type and
constraint below stays identical to SOURCE_OF_TRUTH.md §7.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TANKER_LITRES = 6000


def lpm_to_tankers_per_day(lpm: float) -> float:
    """Litres/minute → BWSSB 6000 L tankers per day, rounded to 1 decimal."""
    return round(lpm * 1440 / TANKER_LITRES, 1)


class Center(BaseModel):
    lat: float
    lng: float


class NetworkNode(BaseModel):
    id: str
    type: Literal["junction", "reservoir", "tank"]
    lat: float
    lng: float
    elevation_m: float
    base_pressure_m: float
    base_demand_lps: float
    households: int
    is_sensor: bool
    leak_probability: float = 0.0


class NetworkPipe(BaseModel):
    id: str
    source: str
    target: str
    diameter_mm: float
    length_m: float
    flow_m3h: float
    status: Literal["OPEN", "CLOSED"]
    road_class: str


class NetworkResponse(BaseModel):
    network_name: str
    center: Center
    sensor_ids: list[str]
    nodes: list[NetworkNode]
    pipes: list[NetworkPipe]


class HealthResponse(BaseModel):
    status: str
    network_name: str
    num_junctions: int
    num_pipes: int
    sensor_ids: list[str]
    matrix_ready: bool


class Preset(BaseModel):
    id: str
    label: str
    description: str
    target_node_id: str
    leak_area_cm2: float = Field(ge=0.5, le=100)
    noise_pct: float = Field(ge=0, le=10)


class PresetsResponse(BaseModel):
    presets: list[Preset]


class LeakSimulationRequest(BaseModel):
    target_node_id: str = Field(min_length=1)
    leak_area_cm2: float = Field(25.0, ge=0.5, le=100)
    noise_pct: float = Field(1.5, ge=0, le=10)
    seed: int | None = Field(None, ge=0)  # numpy default_rng rejects negative seeds


class CandidateNode(BaseModel):
    node_id: str
    confidence_pct: float = Field(ge=0, le=100)
    estimated_loss_lpm: float
    tankers_lost_per_day: float
    estimated_leak_area_cm2: float


class SensorReading(BaseModel):
    sensor_id: str
    baseline_m: float
    observed_m: float
    drop_m: float


class LeakSimulationResponse(BaseModel):
    injected_node_id: str
    injected_leak_area_cm2: float
    actual_loss_lpm: float
    predicted_top_candidates: list[CandidateNode] = Field(min_length=1, max_length=5)
    sensor_readings: list[SensorReading] = Field(min_length=5, max_length=5)
    node_heatmap: dict[str, float]
    recommended_valves_to_close: list[str]
    isolated_node_ids: list[str]
    affected_households_after_isolation: int
    localization_error_m: float
    compute_time_ms: float


class IsolationRequest(BaseModel):
    valve_ids: list[str]
    target_node_id: str = Field(min_length=1)
    leak_area_cm2: float = Field(25.0, ge=0.5, le=100)


class IsolationResponse(BaseModel):
    node_pressures: dict[str, float]
    min_pressure_m: float
    nodes_without_supply: list[str]
