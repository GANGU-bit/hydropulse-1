# Plan 2: GANGU (Backend Assistant, Pipeline and Setup, ~10%)

**Mission:** Unblock everyone fast. You lay the repo foundations so Hardhik can focus on physics, give Badri mock data in the first two hours, and build the "demo insurance" that keeps the pitch alive if something crashes on stage.

## Owned files
`README.md` · `requirements.txt` · `Makefile` · `.gitignore` · `scripts/*.sh` · `data_utils/schemas.py` · `data_utils/geo_mapper.py` · `data_utils/presets.json` · `data_utils/build_presets.py` · `data_utils/make_mock_fixtures.py` · `data_utils/snapshot_fixtures.py` · `tests/test_contract.py` · `frontend/public/mock/*.json` · `RUN_OF_SHOW.md`

## Phases

| # | Window | Deliverable | Done when |
|---|---|---|---|
| 1 | H0–H1 | Repo scaffold, git, requirements, Makefile, `schemas.py` | `make setup` works on a clean machine. Repo pushed |
| 2 | H1–H2 | `geo_mapper.py` + **mock fixtures** for Badri | `frontend/public/mock/` has network/presets/3 simulate files |
| 3 | H3–H5 | `build_presets.py` on Hardhik's real `.inp` | `presets.json` holds 3 real node IDs |
| 4 | H7–H9 | `scripts/smoke_test.sh` + `tests/test_contract.py` | Both pass against the live API |
| 5 | H9–H12 | `snapshot_fixtures.py`: save **real** API responses as mocks | Frontend mock mode replays real results |
| 6 | H12–H16 | Demo laptop: offline install, README, `RUN_OF_SHOW.md` | Whole stack starts with Wi-Fi OFF |
| 7 | H16–H24 | Support Hardhik: run benchmark, test edge cases, log bugs | |

## Handoffs
- **Gives Hardhik:** scaffold + `schemas.py` + `geo_mapper.py` (by H3).
- **Gives Badri:** mock fixtures (H2), then real snapshots (H12).
- **Needs from Hardhik:** `backend/bangalore_kengeri.inp` + `network_meta.json` (H3), live API (H9).

---

## 🎯 Goal Prompt (paste into your agent)

````text
ROLE
You are the backend assistant and dev-pipeline engineer for "HydroPulse", a 24-hour hackathon project
(Lakshya 2026 @ SJBIT Bengaluru, Track 02). HydroPulse is a software-only water-network digital twin. A Python/
FastAPI backend (built by the lead, Hardhik, using US EPA WNTR) simulates a pipe burst in a Kengeri street
network, reads 5 sentinel pressure sensors, triangulates the leak, and recommends valves to close. A React
frontend (built by Badri) shows it on a Leaflet map. Your job is the plumbing around them: repo, setup,
data schemas, coordinate mapping, demo presets, mock data, tests, and demo-day reliability.
Keep your code simple, small and correct. Don't touch backend/ or frontend/src/. Those belong to others.

GOAL
Create and verify the following inside the repo root `hydropulse/`. You're done only when every ACCEPTANCE CHECK passes.

TASK 1: REPO SCAFFOLD (do this first, within ~45 min)
- Folders: backend/ (with __init__.py), backend/tests/, data_utils/ (with __init__.py), scripts/, tests/,
  frontend/public/mock/, pitch_and_roi/.
- .gitignore: venv/, __pycache__/, *.pyc, node_modules/, dist/, .env, backend/sensitivity_matrix.npy,
  backend/sensitivity_matrix.json, .DS_Store
- requirements.txt: wntr, fastapi, uvicorn[standard], networkx, numpy, scipy, scikit-learn, osmnx, pydantic>=2,
  pytest, httpx, requests (pin major versions known to install on Python 3.10–3.12).
- scripts/setup.sh: create venv, pip install -r requirements.txt, cd frontend && npm install (if package.json exists).
- scripts/run_backend.sh: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
- scripts/run_frontend.sh: cd frontend && npm run dev -- --host
- Makefile targets: setup, network (python backend/generate_bangalore_dma.py), backend, frontend, test
  (pytest -q), smoke (scripts/smoke_test.sh), fixtures (python data_utils/make_mock_fixtures.py),
  snapshot (python data_utils/snapshot_fixtures.py), presets (python data_utils/build_presets.py).
- README.md: project one-liner, architecture summary, folder ownership (backend=Hardhik, data_utils+scripts=Gangu,
  frontend=Badri, pitch_and_roi=Jasmine), quickstart, API endpoint list, troubleshooting.
- git init. Branches: main, feat/backend, feat/utils, feat/frontend, feat/pitch. Push to GitHub if the user gives a remote.

TASK 2: data_utils/schemas.py (Pydantic v2). Mirrors API CONTRACT v1.0 EXACTLY.
  Center{lat: float, lng: float}
  NetworkNode{id: str, type: Literal["junction","reservoir","tank"], lat: float, lng: float, elevation_m: float,
              base_pressure_m: float, base_demand_lps: float, households: int, is_sensor: bool,
              leak_probability: float = 0.0}
  NetworkPipe{id, source, target: str, diameter_mm: float, length_m: float, flow_m3h: float,
              status: Literal["OPEN","CLOSED"], road_class: str}
  NetworkResponse{network_name: str, center: Center, sensor_ids: list[str], nodes: list[NetworkNode],
                  pipes: list[NetworkPipe]}
  HealthResponse{status: str, network_name: str, num_junctions: int, num_pipes: int, sensor_ids: list[str],
                 matrix_ready: bool}
  Preset{id: str, label: str, description: str, target_node_id: str,
         leak_area_cm2: float (0.5..100), noise_pct: float (0..10)}
  PresetsResponse{presets: list[Preset]}
  LeakSimulationRequest{target_node_id: str (min_length 1), leak_area_cm2: float = Field(25.0, ge=0.5, le=100),
                        noise_pct: float = Field(1.5, ge=0, le=10), seed: int | None = None}
  CandidateNode{node_id: str, confidence_pct: float (0..100), estimated_loss_lpm: float,
                tankers_lost_per_day: float, estimated_leak_area_cm2: float}
  SensorReading{sensor_id: str, baseline_m: float, observed_m: float, drop_m: float}
  LeakSimulationResponse{injected_node_id: str, injected_leak_area_cm2: float, actual_loss_lpm: float,
                         predicted_top_candidates: list[CandidateNode] (max 5),
                         sensor_readings: list[SensorReading] (exactly 5),
                         node_heatmap: dict[str, float], recommended_valves_to_close: list[str],
                         isolated_node_ids: list[str], affected_households_after_isolation: int,
                         localization_error_m: float, compute_time_ms: float}
  IsolationRequest{valve_ids: list[str], target_node_id: str, leak_area_cm2: float}   (stretch)
  IsolationResponse{node_pressures: dict[str, float], min_pressure_m: float, nodes_without_supply: list[str]}
  Add a docstring with an example to every model (model_config json_schema_extra).
  Helper: lpm_to_tankers_per_day(lpm) = round(lpm * 1440 / 6000, 1). Constant TANKER_LITRES = 6000.

TASK 3: data_utils/geo_mapper.py
  KENGERI_BBOX = dict(lat_min=12.9000, lat_max=12.9160, lng_min=77.4760, lng_max=77.4940)
  BENGALURU_BOUNDS = dict(lat_min=12.70, lat_max=13.30, lng_min=77.30, lng_max=77.90)
  def is_geographic(coords: dict[str, tuple[float,float]]) -> bool   # True if every (x=lng, y=lat) is inside BENGALURU_BOUNDS
  def normalize_to_bbox(coords, bbox=KENGERI_BBOX) -> dict[str, tuple[lat, lng]]
      # linear map of arbitrary (x,y) into the bbox, keeping the aspect ratio and centering. Handle the zero-range case.
  def map_network_coordinates(wn) -> dict[str, tuple[lat, lng]]
      # reads wn.get_node(n).coordinates for every node. Passes through if is_geographic, else normalize_to_bbox.
      # Nodes without coordinates get placed at the centroid of their neighbors.
  def haversine_m(lat1, lng1, lat2, lng2) -> float
  def network_center(latlngs) -> tuple[lat, lng]
  Include tiny doctest-style self-checks under `if __name__ == "__main__":`.

TASK 4: MOCK FIXTURES FOR THE FRONTEND (deliver within 2 hours of start. Badri is blocked without these)
  data_utils/make_mock_fixtures.py writes, validated through the schemas above:
  - frontend/public/mock/network.json: a synthetic 10×8 street grid in KENGERI_BBOX (about 80 junctions J1..J80,
    1 reservoir R1, about 140 pipes). Mixed diameters 400/250/110 mm with road_class primary/secondary/residential.
    Pressures 20–55 m. 5 spread-out sensors.
  - frontend/public/mock/presets.json: 3 presets: "Kengeri Mainline Rupture" (45 cm², 1.5%),
    "Residential Lane Seep" (8 cm², 3%), "Joint Crack under Junction" (20 cm², 2%).
  - frontend/public/mock/simulate_leak_<preset_id>.json for each preset, plus simulate_leak_default.json.
    Make them realistic: the heatmap peaks at the target and decays with grid distance, the top-5 candidates
    are near the target, sensor drops are larger for nearer sensors, the valves are the pipes incident to
    the target, and tankers = lpm × 0.24.
  - frontend/public/mock/health.json
  - frontend/public/mock/README.md explaining the file → endpoint mapping:
    GET /api/network → network.json · GET /api/presets → presets.json · GET /api/health → health.json ·
    POST /api/simulate-leak → simulate_leak_<preset_id>.json (fallback simulate_leak_default.json)

TASK 5: data_utils/build_presets.py (run once backend/bangalore_kengeri.inp exists)
  Load the .inp with wntr (and backend/network_meta.json if present). Pick real junction IDs:
  - mainline: a junction touching the largest-diameter pipe, not a sensor and not next to the reservoir → 45 cm², 1.5%
  - lane seep: a degree-1 (dead-end) junction farthest from the reservoir → 8 cm², 3%
  - joint crack: a junction with median betweenness centrality, not a sensor → 20 cm², 2%
  Write data_utils/presets.json (PresetsResponse shape). Validate through the schemas.

TASK 6: TESTS
  scripts/smoke_test.sh (bash + curl + python -m json.tool). BASE=${BASE:-http://localhost:8000}:
    GET /api/health, GET /api/network (print node/pipe counts), GET /api/presets,
    POST /api/simulate-leak for each preset (print top candidate, confidence, error_m, ms),
    POST with a bad node id → expect 404, POST with leak_area_cm2=500 → expect 422. Exit non-zero on failure.
  tests/test_contract.py (pytest + requests, skips if the API isn't reachable): validates every live response with
    the Pydantic models, checks exactly 5 sensor_readings, heatmap keys == junction ids, heatmap values in [0,1],
    candidates sorted by rank, tankers == round(lpm*0.24, 1) ± 0.2, latency < 1000 ms.

TASK 7: DEMO INSURANCE. data_utils/snapshot_fixtures.py (run once the real API works)
  Call the live API and overwrite frontend/public/mock/{network,presets,health}.json and one
  simulate_leak_<preset_id>.json per preset (seed=7 so results repeat). Then mock mode replays REAL results
  if the backend dies on stage. Print a summary and keep timestamped backups of the old fixtures.

TASK 8: DEMO-DAY READINESS
  RUN_OF_SHOW.md: exact boot order (backend first, wait for "matrix ready", then frontend, then the deck),
  URLs (API :8000/docs, UI :5173, deck file path), the fallback switch (VITE_USE_MOCK=true npm run dev),
  a pre-pitch checklist (power, display mirroring, browser zoom 100%, close notifications, Wi-Fi independence),
  and who does what on stage.
  Verify offline: with Wi-Fi off, the backend boots from the cached .inp + matrix and the frontend runs.
  Pre-download wheels (pip download -r requirements.txt -d wheels/) as a backup.

ACCEPTANCE CHECKS
[ ] Fresh clone → make setup → no errors.
[ ] python -c "import data_utils.schemas, data_utils.geo_mapper" works. geo_mapper self-checks pass.
[ ] make fixtures → all mock JSON files validate against schemas.py.
[ ] make presets → presets.json has 3 node ids that exist in the generated .inp.
[ ] With the backend running: make smoke passes, and pytest tests/test_contract.py passes.
[ ] make snapshot → mock files replaced with real responses.
[ ] RUN_OF_SHOW.md written. Offline boot verified.
Report: files created, commands, check outputs, and anything Hardhik or Badri needs to know.
````
