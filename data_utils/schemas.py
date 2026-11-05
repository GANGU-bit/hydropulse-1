Exit code: 0
Wall time: 2.7 seconds
Output:
"""
Pydantic v2 schemas for HydroPulse.
Mirrors SOURCE_OF_TRUTH.md API CONTRACT v1.0 exactly.
"""

from typing import Annotated, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

# Constants from SOURCE_OF_TRUTH.md Â§6
TANKER_LITRES = 6000
LPCD = 135
PERSONS_PER_HOUSEHOLD = 4.5
NUM_SENSORS = 5
REF_LEAK_CM2 = 25.0
REQUIRED_PRESSURE_M = 15.0
MINIMUM_PRESSURE_M = 2.0
PRESSURE_EXPONENT = 0.5


def lpm_to_tankers_per_day(lpm: float) -> float:
    """Convert loss rate in LPM to tankers lost per day (6000 L/tanker)."""
    return round(float(lpm) * 1440.0 / float(TANKER_LITRES), 1)


class Center(BaseModel):
    """Geographic center coordinates of the network."""
    lat: float = Field(..., description="Latitude in decimal degrees")
    lng: float = Field(..., description="Longitude in decimal degrees")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lat": 12.9081,
                "lng": 77.4852
            }
        }
    )


class NetworkNode(BaseModel):
    """Network junction, reservoir, or tank."""
    id: str = Field(..., description="Unique node ID (e.g., J15, R1)")
    type: Literal["junction", "reservoir", "tank"] = Field(..., description="Node hydraulic type")
    lat: float = Field(..., description="Latitude in decimal degrees")
    lng: float = Field(..., description="Longitude in decimal degrees")
    elevation_m: float = Field(..., description="Node elevation in meters")
    base_pressure_m: float = Field(..., description="Baseline pressure in meters of head (0 for reservoirs/tanks)")
    base_demand_lps: float = Field(..., description="Base water demand in L/s (0 for reservoirs/tanks)")
    households: int = Field(..., description="Estimated connected households (0 for reservoirs/tanks)")
    is_sensor: bool = Field(..., description="Whether this node is one of the 5 sentinel sensors")
    leak_probability: float = Field(0.0, ge=0.0, le=1.0, description="Heatmap leak probability (0..1, always 0.0 in network response)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "J1",
                "type": "junction",
                "lat": 12.90812,
                "lng": 77.48521,
                "elevation_m": 815.2,
                "base_pressure_m": 42.5,
                "base_demand_lps": 0.35,
                "households": 50,
                "is_sensor": True,
                "leak_probability": 0.0
            }
        }
    )


class NetworkPipe(BaseModel):
    """Pipe segment connecting two nodes."""
    id: str = Field(..., description="Unique pipe ID (e.g., P1)")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    diameter_mm: float = Field(..., description="Pipe internal diameter in mm")
    length_m: float = Field(..., description="Pipe length in meters")
    flow_m3h: float = Field(..., description="Pipe flow in m3/h (signed: + = source -> target)")
    status: Literal["OPEN", "CLOSED"] = Field("OPEN", description="Pipe valve status")
    road_class: Literal["primary", "secondary", "residential", "feeder"] = Field(
        ..., description="Associated road classification"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "P1",
                "source": "J1",
                "target": "J2",
                "diameter_mm": 400.0,
                "length_m": 120.5,
                "flow_m3h": 142.8,
                "status": "OPEN",
                "road_class": "primary"
            }
        }
    )


class NetworkResponse(BaseModel):
    """Full network representation for visualization."""
    network_name: str = Field(..., description="Name of the network (e.g. bangalore_kengeri)")
    center: Center = Field(..., description="Network centroid")
    sensor_ids: List[str] = Field(..., description="IDs of sentinel sensors")
    nodes: List[NetworkNode] = Field(..., description="List of all nodes in network")
    pipes: List[NetworkPipe] = Field(..., description="List of all pipes in network")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "network_name": "bangalore_kengeri",
                "center": {"lat": 12.9081, "lng": 77.4852},
                "sensor_ids": ["J12", "J40", "J77", "J103", "J150"],
                "nodes": [],
                "pipes": []
            }
        }
    )


class HealthResponse(BaseModel):
    """Backend service health check response."""
    status: Literal["ok"] = Field("ok", description="Server status")
    network_name: str = Field(..., description="Loaded hydraulic network name")
    num_junctions: int = Field(..., description="Number of junctions")
    num_pipes: int = Field(..., description="Number of pipes")
    sensor_ids: List[str] = Field(..., description="5 sentinel sensor node IDs")
    matrix_ready: bool = Field(..., description="Whether sensitivity matrix is computed and ready")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "network_name": "bangalore_kengeri",
                "num_junctions": 142,
                "num_pipes": 185,
                "sensor_ids": ["J12", "J40", "J77", "J103", "J150"],
                "matrix_ready": True
            }
        }
    )


class Preset(BaseModel):
    """Preset leak scenario for demo and testing."""
    id: str = Field(..., description="Preset ID (e.g., kengeri_mainline_rupture)")
    label: str = Field(..., description="Display label for demo UI")
    description: str = Field(..., description="Description of leak location and severity")
    target_node_id: str = Field(..., description="Target junction ID where leak is injected")
    leak_area_cm2: float = Field(..., ge=0.5, le=100.0, description="Hole area in cm2 (0.5 to 100)")
    noise_pct: float = Field(..., ge=0.0, le=10.0, description="Sensor noise percentage (0 to 10)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "kengeri_mainline_rupture",
                "label": "Kengeri Mainline Rupture",
                "description": "45 cm2 rupture on primary distribution line",
                "target_node_id": "J15",
                "leak_area_cm2": 45.0,
                "noise_pct": 1.5
            }
        }
    )


class PresetsResponse(BaseModel):
    """List of available preset scenarios."""
    presets: List[Preset] = Field(
        ..., min_length=3, max_length=3, description="List of exactly 3 demo presets"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "presets": [
                    {
                        "id": "kengeri_mainline_rupture",
                        "label": "Kengeri Mainline Rupture",
                        "description": "45 cm2 rupture on main trunk",
                        "target_node_id": "J15",
                        "leak_area_cm2": 45.0,
                        "noise_pct": 1.5
                    }
                ]
            }
        }
    )


class LeakSimulationRequest(BaseModel):
    """Request payload for leak simulation."""
    target_node_id: str = Field(..., min_length=1, description="Junction ID to inject leak into")
    leak_area_cm2: float = Field(25.0, ge=0.5, le=100.0, description="Leak orifice area in cm2")
    noise_pct: float = Field(1.5, ge=0.0, le=10.0, description="Gaussian noise level added to sensors (0..10%)")
    seed: Optional[int] = Field(None, description="Optional RNG seed for deterministic noise")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "target_node_id": "J15",
                "leak_area_cm2": 25.0,
                "noise_pct": 1.5,
                "seed": None
            }
        }
    )


class CandidateNode(BaseModel):
    """Candidate leaking junction predicted by triangulation."""
    node_id: str = Field(..., description="Predicted junction ID")
    confidence_pct: float = Field(..., ge=0.0, le=100.0, description="Confidence score 0 to 100")
    estimated_loss_lpm: float = Field(..., description="Estimated water loss in Liters Per Minute")
    tankers_lost_per_day: float = Field(..., description="Equivalent 6000L BWSSB water tankers lost/day")
    estimated_leak_area_cm2: float = Field(..., description="Estimated orifice area in cm2")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "node_id": "J15",
                "confidence_pct": 94.2,
                "estimated_loss_lpm": 480.5,
                "tankers_lost_per_day": 115.3,
                "estimated_leak_area_cm2": 24.1
            }
        }
    )


class SensorReading(BaseModel):
    """Sentinel sensor pressure reading under leak conditions."""
    sensor_id: str = Field(..., description="Sensor junction ID")
    baseline_m: float = Field(..., description="Baseline pressure head in meters")
    observed_m: float = Field(..., description="Observed pressure head with leak and noise in meters")
    drop_m: float = Field(..., description="Pressure drop (baseline - observed) in meters")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sensor_id": "J12",
                "baseline_m": 42.5,
                "observed_m": 36.8,
                "drop_m": 5.7
            }
        }
    )


class LeakSimulationResponse(BaseModel):
    """Full leak simulation and triangulation result."""
    injected_node_id: str = Field(..., description="Actually injected junction ID")
    injected_leak_area_cm2: float = Field(..., description="Injected leak hole area in cm2")
    actual_loss_lpm: float = Field(..., description="Actual water loss in LPM from physics simulation")
    predicted_top_candidates: List[CandidateNode] = Field(
        ..., min_length=1, max_length=5, description="Top 1 to 5 candidates, index 0 is best"
    )
    sensor_readings: List[SensorReading] = Field(..., min_length=5, max_length=5, description="Sentinel sensor readings (exactly 5)")
    node_heatmap: Dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        ..., description="Heatmap 0..1 for every junction ID"
    )
    recommended_valves_to_close: List[str] = Field(..., description="Sorted list of pipe IDs to close for isolation")
    isolated_node_ids: List[str] = Field(..., description="Junctions cut off from supply after valve isolation")
    affected_households_after_isolation: int = Field(..., description="Total households cut off after isolation")
    localization_error_m: float = Field(..., description="Haversine distance in meters between injected and top candidate")
    compute_time_ms: float = Field(..., description="Total calculation time in milliseconds")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "injected_node_id": "J15",
                "injected_leak_area_cm2": 25.0,
                "actual_loss_lpm": 482.1,
                "predicted_top_candidates": [
                    {
                        "node_id": "J15",
                        "confidence_pct": 94.2,
                        "estimated_loss_lpm": 480.5,
                        "tankers_lost_per_day": 115.3,
                        "estimated_leak_area_cm2": 24.1
                    }
                ],
                "sensor_readings": [
                    {"sensor_id": "J12", "baseline_m": 42.5, "observed_m": 36.8, "drop_m": 5.7},
                    {"sensor_id": "J40", "baseline_m": 38.2, "observed_m": 37.1, "drop_m": 1.1},
                    {"sensor_id": "J77", "baseline_m": 45.0, "observed_m": 44.2, "drop_m": 0.8},
                    {"sensor_id": "J103", "baseline_m": 31.4, "observed_m": 30.9, "drop_m": 0.5},
                    {"sensor_id": "J150", "baseline_m": 36.7, "observed_m": 35.8, "drop_m": 0.9}
                ],
                "node_heatmap": {"J1": 0.12, "J15": 1.0},
                "recommended_valves_to_close": ["P14", "P19"],
                "isolated_node_ids": ["J15"],
                "affected_households_after_isolation": 42,
                "localization_error_m": 0.0,
                "compute_time_ms": 312.4
            }
        }
    )


class IsolationRequest(BaseModel):
    """Optional stretch: request to isolate specified valves."""
    valve_ids: List[str] = Field(..., description="Pipes to close")
    target_node_id: str = Field(..., description="Target leak junction ID")
    leak_area_cm2: float = Field(..., description="Leak hole area in cm2")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "valve_ids": ["P14", "P19"],
                "target_node_id": "J15",
                "leak_area_cm2": 25.0
            }
        }
    )


class IsolationResponse(BaseModel):
    """Optional stretch: post-isolation network pressure and disconnected nodes."""
    node_pressures: Dict[str, float] = Field(..., description="Pressure head per node after isolation")
    min_pressure_m: float = Field(..., description="Minimum remaining pressure head in meters")
    nodes_without_supply: List[str] = Field(..., description="Nodes completely disconnected from supply")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "node_pressures": {"J1": 39.5, "J2": 37.2},
                "min_pressure_m": 16.4,
                "nodes_without_supply": ["J15"]
            }
        }
    )

