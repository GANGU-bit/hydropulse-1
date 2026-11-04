# Plan 1: HARDHIK (Lead Backend Developer, ~70%)

**Mission:** Build the hydraulic brain of HydroPulse: a real Kengeri pipe network, a WNTR pressure-driven-demand (PDD) digital twin, 5 sentinel sensors, a leak triangulator, valve isolation, and the FastAPI server. You also **own integration**: the API contract and merges to `main`.

## Owned files
`backend/generate_bangalore_dma.py` · `backend/hydraulic_engine.py` · `backend/main.py` · `backend/benchmark.py` · `backend/tests/` · generated: `backend/bangalore_kengeri.inp`, `backend/network_meta.json`, `backend/sensitivity_matrix.npy`, `backend/benchmark_report.md`

## Phases

| # | Window | Deliverable | Done when |
|---|---|---|---|
| 1 | H0–H1 | Env plus WNTR smoke test on built-in Net3 with leak injection | A leak at one Net3 junction gives `leak_demand > 0` |
| 2 | H1–H3 | `generate_bangalore_dma.py` | Writes `.inp` + `network_meta.json`. 60–250 junctions, one connected component, baseline pressures 15–70 m |
| 3 | H3–H5 | Engine core: load, PDD, baseline, sensor selection | 5 distinct, separated junction sensors. Baseline solves in under 100 ms |
| 4 | H5–H7 | Sensitivity matrix, stage-1 cosine, stage-2 verification | With no noise, reference leaks localize top-1 on ≥ 90% of nodes |
| 5 | H7–H9 | `main.py` endpoints per contract v1.0 | Swagger `/docs` works. `simulate-leak` p95 under 1 s |
| — | **H9** | **CP1: integrate with Badri's frontend** | Map renders the real network |
| 6 | H9–H11 | Valve isolation + affected households | Isolation cuts the leak node off from all sources |
| 7 | H11–H13 | `benchmark.py` + pytest suite | `benchmark_report.md` produced. All tests green |
| — | **H13** | **CP2: send benchmark numbers to Jasmine. Freeze API shape** | |
| 8 | H13–H18 | Robustness, perf, stretch: `/api/isolate`, sensitivity-greedy sensors | No 500s across 200 random requests |
| 9 | H18–H24 | Bug fixes only, tag `v1.0-demo` | Dress rehearsal passes |

## Handoffs
- **Needs from Gangu:** repo scaffold + `data_utils/schemas.py` + `data_utils/geo_mapper.py` by H3. If they're late, write minimal stand-ins with the same interface, then swap.
- **Gives Gangu:** the `.inp` + `network_meta.json` at H3 so he can build presets on the real network.
- **Gives Badri:** a running API at H9 (`uvicorn backend.main:app --port 8000`).
- **Gives Jasmine:** `backend/benchmark_report.md` at H13.

---

## 🎯 Goal Prompt (paste into your agent)

````text
ROLE
You are the lead backend engineer for "HydroPulse", a 24-hour hackathon project (Lakshya 2026 @ SJBIT,
Bengaluru, Track 02 Smart & Sustainable Future). HydroPulse is a software-only hydraulic digital twin. It
locates underground municipal water-pipe bursts in a Bengaluru ward (Kengeri, next to SJBIT) using pressure
readings from only 5 "sentinel" junctions, then recommends which valves to close. NO hardware of any kind.

GOAL
Inside the repo root `hydropulse/`, build and verify a working Python backend:
  1. backend/generate_bangalore_dma.py : OpenStreetMap streets → EPANET pipe network (.inp)
  2. backend/hydraulic_engine.py       : WNTR PDD engine, sentinel sensors, sensitivity matrix, triangulation, isolation
  3. backend/main.py                   : FastAPI server implementing API contract v1.0 (below) exactly
  4. backend/benchmark.py              : honest accuracy/latency benchmark → backend/benchmark_report.md
  5. backend/tests/                    : pytest suite
You are done only when every ACCEPTANCE CHECK at the bottom passes and you have actually run it.

ENVIRONMENT
Python 3.10+. pip install wntr fastapi "uvicorn[standard]" networkx numpy scipy scikit-learn osmnx pydantic pytest httpx
Run everything from the repo root. Packages `backend/` and `data_utils/` each have an __init__.py.
Imports: `from backend.hydraulic_engine import HydraulicEngine`, `from data_utils.schemas import ...`,
`from data_utils.geo_mapper import map_network_coordinates, haversine_m`.
A teammate (Gangu) owns data_utils/. If data_utils/schemas.py or geo_mapper.py don't exist yet, create minimal
versions with exactly the interfaces described below and tell the user they're temporary stand-ins.

NON-NEGOTIABLE INVARIANTS
- wn.options.time.duration = 0 (single steady-state snapshot). Never run multi-hour simulations.
- wn.options.hydraulic.demand_model = 'PDD'; required_pressure = 15.0; minimum_pressure = 2.0; pressure_exponent = 0.5.
- Leaks ONLY work with wntr.sim.WNTRSimulator (EpanetSimulator silently ignores them). Always use WNTRSimulator.
- Leak injection: node.add_leak(wn, area=area_m2, discharge_coeff=0.75, start_time=0). Area MUST be m²:
  area_m2 = leak_area_cm2 / 10000.0. Reference leak for the matrix = 25 cm² = 0.0025 m².
- Candidate leak nodes and sensors come ONLY from wn.junction_name_list (never reservoirs/tanks).
- Never mutate the pristine model across requests without restoring it. Either deepcopy a pristine model per
  run, or add_leak → run → remove_leak + wn.reset_initial_values() under a threading.Lock. Endpoints are sync `def`.
- WNTR API differences between versions: write the .inp with wntr.network.write_inpfile(wn, path) and fall back
  to wn.write_inpfile(path). Get the graph with wn.to_graph() or wntr.network.to_graph(wn). Then
  convert to an undirected nx.Graph.
- OSMnx API differences between versions: ox.convert.to_undirected (v2) vs ox.get_undirected (v1). Largest component
  via ox.truncate.largest_component(G, strongly=False) (v2) or ox.utils_graph.get_largest_component (v1), or just
  G.subgraph(max(nx.connected_components(Gu), key=len)).

──────────────── PART 1: backend/generate_bangalore_dma.py ────────────────
- CLI flags: --lat 12.9081 --lng 77.4852 --dist 600 --seed 42 --out backend/bangalore_kengeri.inp
- Download the drive network: ox.graph_from_point((lat,lng), dist=dist, network_type='drive'). Convert to
  undirected and keep the largest connected component. Drop self-loops. Collapse parallel edges, keeping the shortest.
- If the network has fewer than 60 or more than 250 intersections, adjust dist automatically and retry (max 4 tries).
- Rename nodes to J1..JN and edges to P1..PM (deterministic order). Keep osmid in the metadata.
- Junctions: coordinates = (x=lng, y=lat) exactly as OSM gives them. Synthetic elevation =
  815 m + a gentle planar slope (about 15 m across the bbox) + N(0, 0.5) noise, seeded. Document this as a proxy.
  Households per junction: uniform random 10–80 (seeded). base_demand (m³/s) = households × 4.5 × 135 L/day
  converted to m³/s.
- Pipes: length = OSM 'length' (minimum 1 m). Roughness 120 (Hazen-Williams). Diameter by 'highway' (the value may
  be a list, so take the first): trunk/primary = 0.40 m, secondary/tertiary = 0.25 m, everything else = 0.11 m.
- Source: reservoir "R1" placed about 50 m off the highest-elevation junction, connected by feeder pipe "P0"
  (0.50 m, 50 m). base_head = max_elevation + 45 m.
- Validate with a baseline WNTRSimulator run. If any junction is below 15 m, raise the reservoir head in 5 m steps
  (and, if needed, upsize 0.11 m pipes to 0.15 m) until the minimum is ≥ 15 m and the maximum is ≤ 70 m.
  Print a summary: counts, min/mean/max pressure, total demand.
- Also write backend/network_meta.json:
  {network_name, center{lat,lng}, households{Jx:int}, road_class{Px:str}, osmid{Jx:int}, generated_at, params}
- Remember that .inp files never store leaks or households, which is why the metadata file exists.

──────────────── PART 2: backend/hydraulic_engine.py ────────────────
class HydraulicEngine(inp_path="backend/bangalore_kengeri.inp", meta_path="backend/network_meta.json", num_sensors=5)
- __init__: load the .inp if it exists, otherwise fall back to WNTR's bundled Net3 (find the examples path inside the
  wntr package, or ship a copy) and log a loud WARNING. Apply the invariants. Load meta if present. Otherwise derive
  households = round(base_demand_lpd / (135*4.5)), clamped to 1–500, and road_class from diameter.
  Lat/lng per node come from data_utils.geo_mapper.map_network_coordinates(wn) → {node_id: (lat, lng)}.
  It passes real Bengaluru coordinates through untouched and maps Net3's arbitrary coordinates into the
  Kengeri bbox.
- run_baseline(): solve with no leak. Cache the full junction pressure series, pipe flows (m³/h), and P_base (5 sensors).
- select_sentinel_sensors(min_hop_separation=3): betweenness centrality on the undirected pipe graph,
  weight = pipe length, junctions only. Greedy: walk nodes by descending centrality and accept a node only if
  its hop distance to every already-chosen sensor is ≥ min_hop_separation. If you can't get 5, relax the
  separation by 1 and repeat. Optional strategy="sensitivity_greedy" (stretch): build the full N×N pressure-drop
  matrix, then greedily pick the sensors that maximize top-1 self-localization accuracy.
- build_or_load_sensitivity_matrix(): for each junction j, inject the 25 cm² reference leak, solve, and store
  s_j = P_base − P_j (5 values). Result S has shape N×5. Also store each j's leak_demand at the reference size.
  Cache to backend/sensitivity_matrix.npy plus a sidecar .json holding the sha256 of the .inp, sensor ids, junction
  order and reference area. Reuse the cache only if all of them match. Log the build time.
- simulate_and_triangulate(target_node_id, leak_area_cm2, noise_pct, seed=None) -> dict matching the contract:
  a. Validate: target must be in junction_name_list (raise KeyError → the API returns 404), area 0.5–100,
     noise 0–10.
  b. Inject the leak at the target, solve, read the 5 sensor pressures and the target's leak_demand →
     actual_loss_lpm = m³/s × 60000.
  c. Noise: dp_true = P_base − P_obs_clean. sigma = (noise_pct/100) × max(mean(|dp_true|), 0.05 m).
     P_obs = P_obs_clean + N(0, sigma) per sensor (np.random.default_rng(seed)). dp_obs = P_base − P_obs.
  d. Stage 1 (screening): cosine similarity between dp_obs and every row of S (guard zero-norm rows/vectors).
     node_heatmap = clip(cos, 0, 1) ** 8, normalized so the max is 1.0, for ALL junctions.
  e. Stage 2 (verification): for the top K=5 stage-1 candidates, estimate a scale
     alpha = (dp_obs·s_j)/(s_j·s_j) and est_area_cm2 = clamp(25 × alpha, 0.5, 100). Re-simulate a leak of that
     size at j, get dp_sim_j, compute residual r_j = ||dp_obs − dp_sim_j||₂ and record the sim's leak_demand.
     Re-rank by r_j ascending. confidence_pct = 100 × cosine(dp_obs, dp_sim_j), rounded to 1 decimal.
     estimated_loss_lpm comes from the verification sim. tankers_lost_per_day = lpm × 1440 / 6000.
  f. localization_error_m = haversine distance between the injected node and the top candidate.
  g. Isolation (below) on the top candidate. compute_time_ms = wall time of the whole call.
- isolation_plan(node_id): assume a valve on every pipe. recommended_valves_to_close = all OPEN pipes incident to
  node_id (sorted). Remove them from the undirected graph. Any junction no longer connected to a reservoir or
  tank goes into isolated_node_ids. affected_households_after_isolation = the sum of their households.
- get_network_payload(): builds the GET /api/network body (contract below). leak_probability = 0.0.
- (stretch) simulate_isolation(valve_ids, target_node_id, leak_area_cm2): close the pipes
  (initial_status = wntr.network.LinkStatus.Closed), keep the leak, solve, and return node pressures, the minimum
  pressure, and the nodes without supply.

──────────────── PART 3: backend/main.py (FastAPI) ────────────────
- app = FastAPI(title="HydroPulse API", version="1.0"). CORSMiddleware allows all origins/methods/headers.
- Build a single HydraulicEngine at startup (lifespan): baseline → sensors → matrix (load cache if valid).
- Endpoints and response_model come from data_utils/schemas.py:
  GET  /api/health          → {status:"ok", network_name, num_junctions, num_pipes, sensor_ids, matrix_ready}
  GET  /api/network         → NetworkResponse
  GET  /api/presets         → reads data_utils/presets.json. Drop presets whose node id doesn't exist in the
                              loaded network. If none remain, auto-generate 3 (largest-diameter-adjacent node
                              45 cm²/1.5%, a degree-1 node 8 cm²/3%, a median-centrality node 20 cm²/2%).
  POST /api/simulate-leak   → LeakSimulationResponse. 404 for unknown junction, 422 for validation errors.
  POST /api/isolate         → (stretch) IsolationResponse
- Use a threading.Lock around WNTR calls. Log one line per request with node, area and ms.
- Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000

API CONTRACT v1.0 (exact field names. The frontend depends on them.)
GET /api/network
{ "network_name": "bangalore_kengeri", "center": {"lat": 12.9081, "lng": 77.4852},
  "sensor_ids": ["J12","J40","J77","J103","J150"],
  "nodes": [ { "id":"J1","type":"junction","lat":12.90812,"lng":77.48521,"elevation_m":815.2,
               "base_pressure_m":42.5,"base_demand_lps":0.35,"households":50,"is_sensor":true,
               "leak_probability":0.0 } ],        // type ∈ junction|reservoir|tank. Reservoirs: households 0
  "pipes": [ { "id":"P1","source":"J1","target":"J2","diameter_mm":400.0,"length_m":120.5,
               "flow_m3h":142.8,"status":"OPEN","road_class":"primary" } ] }
POST /api/simulate-leak  request: {"target_node_id":"J15","leak_area_cm2":25.0,"noise_pct":1.5,"seed":null}
response:
{ "injected_node_id":"J15", "injected_leak_area_cm2":25.0, "actual_loss_lpm":482.1,
  "predicted_top_candidates":[ {"node_id":"J15","confidence_pct":94.2,"estimated_loss_lpm":480.5,
                                "tankers_lost_per_day":115.3,"estimated_leak_area_cm2":24.1} ],   // ≤5, best first
  "sensor_readings":[ {"sensor_id":"J12","baseline_m":42.5,"observed_m":36.8,"drop_m":5.7} ],     // exactly 5
  "node_heatmap":{"J1":0.12,"J15":1.0},            // every junction, 0..1
  "recommended_valves_to_close":["P14","P19"], "isolated_node_ids":["J15"],
  "affected_households_after_isolation":42, "localization_error_m":0.0, "compute_time_ms":312.4 }
Round floats to 2 decimals (lat/lng to 6).

──────────────── PART 4: backend/benchmark.py ────────────────
- For every junction (or a seeded sample of 80 if N > 80): random area U(10, 60) cm², noise 1.5%, fixed seed.
  Record top-1 hit, top-3 hit, localization_error_m, compute_time_ms.
- Repeat at noise 0%, 1.5%, 3% and 5%. Compare sensor strategies (betweenness-separated vs plain top-5 betweenness,
  plus sensitivity_greedy if implemented).
- Write backend/benchmark_report.md with a table: top-1 %, top-3 %, mean/median/p90 error (m), p50/p95 latency (ms),
  network size, sensor ids. Add a one-paragraph plain-English summary the pitch team can quote.
  NEVER inflate the numbers. The pitch will quote this file.

──────────────── PART 5: backend/tests/ (pytest) ────────────────
- test_units: 25 cm² → 0.0025 m² path verified (the leak_demand order of magnitude is plausible, < 2000 LPM at ~40 m).
- test_sensors: exactly 5, distinct, all junctions, separation respected.
- test_self_localization: noise 0 at the reference area → top-1 correct on ≥ 90% of junctions.
- test_reservoir_rejected: simulate on "R1" → KeyError / 404.
- test_api (fastapi.testclient): /api/health, /api/network schema, /api/simulate-leak happy path + 404 + 422
  (area 0 and area 500).
- test_isolation: after removing the recommended valves, the leak node can't reach any source.

ACCEPTANCE CHECKS (run them all and paste the outputs into your final report)
[ ] python backend/generate_bangalore_dma.py → .inp + meta written. 60–250 junctions. Min pressure ≥ 15 m.
[ ] Engine boot (cold, building the matrix) < 60 s. Warm boot (cache hit) < 5 s.
[ ] curl /api/health → matrix_ready true, 5 sensor_ids.
[ ] curl -X POST /api/simulate-leak with a preset → valid JSON. p95 latency < 1000 ms over 50 calls.
[ ] pytest backend/tests -q → all green.
[ ] backend/benchmark_report.md exists with real numbers.
[ ] Net3 fallback works if the .inp is deleted (warning logged).
[ ] Nothing uses EpanetSimulator for leaks, duration is 0 everywhere, and no cm² reaches WNTR unconverted.

WORKING STYLE
Build incrementally and run code after every step. Never claim something works without running it. Keep
functions small and typed, with docstrings. Comment the key physics: Torricelli Q = Cd·A·sqrt(2g·h), PDD, cosine.
When finished, report: files created, the commands to run, acceptance check outputs, benchmark headline numbers,
and any known limitations.
````
