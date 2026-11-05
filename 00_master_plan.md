# HydroPulse: 24h Team Execution Plan (Master Index)

**Event:** Lakshya 2026 @ SJBIT, Bengaluru · **Track 02:** Smart and Sustainable Future
**Product:** A software-only hydraulic digital twin. It finds underground pipe bursts using 5 sentinel pressure nodes and produces a valve-isolation plan.

| Member | Role | Workload | Plan file |
|---|---|---|---|
| **Hardhik** | Lead backend developer: physics engine, triangulation, API, integration owner | **~70%** | [01_hardhik_plan.md](file:///home/rosso/.gemini/antigravity/brain/05ea83ea-9dec-4fbc-8522-665a8d13b9a2/01_hardhik_plan.md) |
| **Gangu** | Backend assistant: repo/env setup, schemas, geo-mapper, presets, mock fixtures, smoke tests, demo-laptop setup | **~10%** | [02_gangu_plan.md](file:///home/rosso/.gemini/antigravity/brain/05ea83ea-9dec-4fbc-8522-665a8d13b9a2/02_gangu_plan.md) |
| **Badri** | All frontend work and the SCADA design system | **~15%** | [03_badri_plan.md](file:///home/rosso/.gemini/antigravity/brain/05ea83ea-9dec-4fbc-8522-665a8d13b9a2/03_badri_plan.md) |
| **Jasmine** | Animated HTML pitch deck, ROI calculator, script, Q&A sheet | **~5%** | [04_jasmine_plan.md](file:///home/rosso/.gemini/antigravity/brain/05ea83ea-9dec-4fbc-8522-665a8d13b9a2/04_jasmine_plan.md) |

Every plan file ends with a **🎯 Goal Prompt** block. Each prompt stands on its own, so you can paste it straight into an agent (Cursor, Windsurf, Claude, Antigravity) without any other context.

---

## 1. Corrections to the audit dossier (already applied in all four prompts)

> [!WARNING]
> The dossier calls the code "Verified Designed", but **no repository exists yet**. Every plan assumes we build from an empty folder.

1. **Leaks only work with `WNTRSimulator`.** `EpanetSimulator` ignores `add_leak()`. Every prompt also passes `start_time=0` explicitly.
2. **The "94.2% confidence" figure is not accuracy.** Hardhik writes `backend/benchmark.py`, which measures top-1 and top-3 accuracy and localization error in metres. Jasmine's slides use **only those measured numbers**.
3. **Plain top-5 betweenness tends to cluster sensors** on one trunk road, which makes nodes hard to tell apart. The fix keeps betweenness but adds a **minimum hop-separation** rule (default 3 hops).
4. **Cosine similarity is only roughly scale-invariant**, because the orifice physics is non-linear. The fix adds a **stage-2 verification step**: re-simulate the top 5 candidates at their estimated leak size and re-rank by residual. This adds about 5 × 30 ms.
5. **"Within 20 m" is not guaranteed.** Kengeri OSM intersections sit 50–150 m apart, so localization is node-level. The pitch should claim "pinpoints the junction / city block" unless the benchmark proves something tighter.
6. **Every pitch statistic** (₹50 crore, 500 MLD, 35–40% NRW) gets a `[VERIFY]` tag until someone finds a source.
7. **Demo insurance:** once the backend works, real API responses are saved as JSON fixtures. If the backend crashes on stage, the frontend switches to mock mode and replays **real** results.

---

## 2. Repository layout and ownership

```text
hydropulse/                          # git root (Gangu creates it at H0)
├── backend/          → HARDHIK      main.py, hydraulic_engine.py, generate_bangalore_dma.py,
│                                    benchmark.py, bangalore_kengeri.inp, network_meta.json,
│                                    sensitivity_matrix.npy (cache, gitignored), tests/
├── data_utils/       → GANGU        schemas.py, geo_mapper.py, presets.json, build_presets.py,
│                                    make_mock_fixtures.py, snapshot_fixtures.py
├── scripts/          → GANGU        setup.sh, run_backend.sh, run_frontend.sh, smoke_test.sh
├── tests/            → GANGU        test_contract.py (live-API contract tests)
├── frontend/         → BADRI        Vite + React 18 + Tailwind + react-leaflet + Recharts
│   └── public/mock/  → GANGU writes fixtures, BADRI reads them
├── pitch_and_roi/    → JASMINE      deck/index.html, deck/styles.css, deck/deck.js,
│                                    pitch_script.md, qa_defense.md, architecture.mmd
├── Makefile, requirements.txt, README.md, .gitignore → GANGU
```

**Git rules:** one branch per person (`feat/backend`, `feat/utils`, `feat/frontend`, `feat/pitch`). Hardhik merges into `main`. Nobody edits a folder they don't own without telling its owner.

**PR workflow (mandatory from the backend MVP onward):** every change lands on `main` through a small pull request: one logical change, stacked if it builds on an unmerged PR. Mark it *Ready for review*. Hardhik reviews and merges. No direct pushes to `main`. Full rules: `SOURCE_OF_TRUTH.md` §10.

---

## 3. Shared API contract v1.0 (frozen at H1)

> [!IMPORTANT]
> This section is a summary. The full canonical contract is **`SOURCE_OF_TRUTH.md` in [§5](#5-source_of_truthmd-copy-into-the-repo-root)**. If the two ever disagree, `SOURCE_OF_TRUTH.md` wins. Only Hardhik can change the contract, following the change protocol in SOT §11. Gangu's `schemas.py` is the code version of it.

- `GET /api/health` → `{status, network_name, num_junctions, num_pipes, sensor_ids[5], matrix_ready}`
- `GET /api/network` → `{network_name, center{lat,lng}, sensor_ids[], nodes[], pipes[]}`
  - node: `{id, type: junction|reservoir|tank, lat, lng, elevation_m, base_pressure_m, base_demand_lps, households, is_sensor, leak_probability}`
  - pipe: `{id, source, target, diameter_mm, length_m, flow_m3h, status: OPEN|CLOSED, road_class}`
- `GET /api/presets` → `{presets: [{id, label, description, target_node_id, leak_area_cm2, noise_pct}]}`
- `POST /api/simulate-leak`
  - request: `{target_node_id, leak_area_cm2 (0.5–100), noise_pct (0–10), seed?: int|null}`
  - response: `{injected_node_id, injected_leak_area_cm2, actual_loss_lpm, predicted_top_candidates[≤5]: {node_id, confidence_pct, estimated_loss_lpm, tankers_lost_per_day, estimated_leak_area_cm2}, sensor_readings[5]: {sensor_id, baseline_m, observed_m, drop_m}, node_heatmap{junction_id: 0..1}, recommended_valves_to_close[], isolated_node_ids[], affected_households_after_isolation, localization_error_m, compute_time_ms}`
  - errors: `404 {"detail": "Unknown junction id: X"}`, `422` validation
- *(stretch, optional)* `POST /api/isolate` → `{node_pressures{id: m}, min_pressure_m, nodes_without_supply[]}`

**Fixed formulas:** `tankers_lost_per_day = lpm × 1440 / 6000` · `area_m2 = cm2 / 10000` · households per junction = `base_demand_lpd / (135 LPCD × 4.5 persons)`.

---

## 4. Timeline and checkpoints (H0 = hackathon start)

| Window | Hardhik | Gangu | Badri | Jasmine |
|---|---|---|---|---|
| H0–H1 | Install env, smoke-test WNTR on Net3 | Repo scaffold, requirements, Makefile, `schemas.py` | Vite scaffold, Tailwind tokens, layout shell | Story outline, slide list, gather sourced stats |
| H1–H3 | `generate_bangalore_dma.py` → valid `.inp` with healthy pressures | `geo_mapper.py`, **mock fixtures by H2** | MapCanvas on mock data | Deck engine (nav, scaling, animation system) |
| H3–H7 | Engine: baseline, sensors, sensitivity matrix, stage-1/2 triangulation | `build_presets.py` on real network, contract tests, help debug | ControlPanel, PressureCharts, IncidentPanel | Slides 1–7 with animations |
| H7–H9 | `main.py` FastAPI, error handling | `smoke_test.sh`, run scripts | API client, loading/error states, mock fallback | ROI calculator slide, architecture slide |
| **H9 ✅ CP1** | **Live integration: frontend switches from mock to the real API** | | | |
| H9–H13 | Valve isolation, `benchmark.py`, pytest, perf | Snapshot real fixtures, demo-laptop offline prep | Isolation animation, heatmap, judge mode, ROI panel | Demo slide, team slide, backup slides |
| **H13 ✅ CP2** | **Benchmark numbers → Jasmine. API shape frozen.** | | | |
| H13–H18 | Edge cases, robustness, stretch (`/api/isolate`) | README, offline checklist | Polish, test on 1366×768 and 1920×1080 projector | Pitch script, Q&A sheet, record backup demo video |
| **H18 ✅ CP3** | **Full dress rehearsal (deck → live demo → Q&A)** | | | |
| H18–H22 | Bug fixes only | Run-of-show checklist | Bug fixes only | Rehearse 3× with a timer |
| H22–H24 | **Code freeze.** Tag `v1.0-demo` | Final laptop check | | Final rehearsal |

```mermaid
flowchart LR
    G1["Gangu: scaffold + schemas (H1)"] --> H1["Hardhik: DMA generator (H3)"]
    G2["Gangu: mock fixtures (H2)"] --> B1["Badri: UI on mocks"]
    H1 --> H2["Hardhik: engine + API (H9)"]
    H1 --> G3["Gangu: presets on real network"]
    H2 --> CP1["CP1: live integration (H9)"]
    B1 --> CP1
    H2 --> H3["Hardhik: benchmark (H13)"]
    H3 --> J1["Jasmine: results slide"]
    CP1 --> G4["Gangu: snapshot real fixtures"]
    G4 --> CP3["CP3: dress rehearsal (H18)"]
    J1 --> CP3
```

> [!TIP]
> These prompts are built for long autonomous runs. In Antigravity, start each one with the `/goal` command so the agent keeps going until every acceptance check passes.

---

## 5. `SOURCE_OF_TRUTH.md` (copy into the repo root)

**How to use it:**
1. At H0, Gangu commits the file below as `hydropulse/SOURCE_OF_TRUTH.md`, plus the two small pointer files after it (`AGENTS.md` and `.gitattributes`).
2. Add this line to the **top of all four goal prompts** before pasting them into an agent:
   > `FIRST read SOURCE_OF_TRUTH.md in the repo root. It overrides this prompt wherever they conflict. Follow its ownership, naming, unit, contract and git rules exactly.`
3. When an agent or teammate is unsure about a field, unit, port, ID or path, the answer comes from this file, not from memory or chat.

````markdown
# HydroPulse: SOURCE OF TRUTH
Version: 1.0.1 · Contract owner: Hardhik · Last updated: backend MVP · Status: FROZEN after CP2 (H13)

> This file is the single source of truth for every human and AI agent working on HydroPulse.
> If code, a prompt, a plan, a chat message or an agent's assumption disagrees with this file, THIS FILE WINS
> and the code is wrong. Never edit this file unless you follow §11.

---------------------------------------------------------------------------------------------------
## 0. Rules for AI agents and humans (read before writing any code)
1. Read this whole file before starting. Re-read §5–§7 before touching anything that crosses a boundary
   (API, fixtures, presets, schemas).
2. Only edit files inside the folders you own (§2). If you need a change elsewhere, write a request in
   `HANDOFF.md` (§13) and tell the owner.
3. Never rename, remove, re-type or re-case an API field, endpoint, ID scheme, port, path or constant.
4. Never add a dependency without asking the owner of the manifest (requirements.txt = Gangu,
   frontend/package.json = Badri).
5. Never invent data. Don't fabricate statistics, accuracy numbers or node IDs. Real IDs come from the committed
   `backend/bangalore_kengeri.inp`. Real accuracy comes from `backend/benchmark_report.md`.
6. Never claim "done" without running the checks in §12 for your role.
7. When in doubt, stop and ask. Don't guess a field name.

---------------------------------------------------------------------------------------------------
## 1. Project in one paragraph
HydroPulse is a software-only hydraulic digital twin for Lakshya 2026 (SJBIT, Track 02). It turns Kengeri
OpenStreetMap streets into an EPANET pipe network, simulates a pipe burst with US EPA WNTR (pressure-driven
demand, Torricelli orifice leak), watches only 5 sentinel junctions (betweenness centrality with hop separation),
matches the 5-sensor pressure-drop fingerprint against precomputed fingerprints (a size-aware fit over a grid of
leak areas, then a verification re-simulation; cosine similarity drives the heatmap), pinpoints the leaking junction, reports the loss in LPM and BWSSB tankers per day,
and recommends valves to close. No hardware.

---------------------------------------------------------------------------------------------------
## 2. Ownership matrix (who may edit what)
| Path                                   | Owner    | Others may edit?                                  |
|----------------------------------------|----------|---------------------------------------------------|
| backend/**                             | Hardhik  | No                                                |
| backend/bangalore_kengeri.inp, network_meta.json | Hardhik | No. Regenerating needs team approval (§9)     |
| data_utils/**                          | Gangu    | No (Hardhik may read/import)                      |
| scripts/**, Makefile, README.md        | Gangu    | Add a line to README only, via PR                 |
| requirements.txt                       | Gangu    | Request via HANDOFF.md                            |
| tests/** (root contract tests)         | Gangu    | No                                                |
| frontend/** (except public/mock/)      | Badri    | No                                                |
| frontend/package.json, package-lock.json | Badri  | No                                                |
| frontend/public/mock/**                | Gangu    | Badri may ADD extra files, never edit Gangu's     |
| frontend/screenshots/**                | Badri    | No                                                |
| pitch_and_roi/**                       | Jasmine  | No                                                |
| SOURCE_OF_TRUTH.md                     | Hardhik  | Proposals only (§11)                              |
| HANDOFF.md                             | Everyone | Append only, never delete others' entries         |
| .gitignore, .gitattributes, AGENTS.md  | Gangu    | Request via HANDOFF.md                            |

---------------------------------------------------------------------------------------------------
## 3. Environment and tooling
| Item            | Value                                                                        |
|-----------------|------------------------------------------------------------------------------|
| Python          | 3.11 (3.10–3.12 allowed). Virtualenv folder: `.venv/` at the repo root       |
| Node            | 20 LTS. Package manager: **npm only** (no yarn/pnpm, to avoid lockfile conflicts) |
| Working dir     | ALWAYS run Python commands from the repo root (`hydropulse/`)                |
| Python imports  | Absolute only: `from backend.hydraulic_engine import ...`, `from data_utils.schemas import ...` |
| Packages        | `backend/__init__.py` and `data_utils/__init__.py` must exist                |
| Encoding        | UTF-8 everywhere. Line endings LF (enforced by .gitattributes)              |
| Python style    | Type hints and docstrings. Formatting with black defaults (88 cols) if available |
| JS style        | Prettier defaults, 2 spaces, semicolons, single quotes                      |
| Secrets         | None needed. Never commit .env files                                         |

---------------------------------------------------------------------------------------------------
## 4. Ports, URLs, env vars and commands
| Thing                 | Value                                                          |
|-----------------------|----------------------------------------------------------------|
| Backend               | http://localhost:8000 (docs at /docs). Bind 0.0.0.0            |
| Frontend (Vite)       | http://localhost:5173. Vite proxies `/api` → http://localhost:8000 |
| Deck                  | open `pitch_and_roi/deck/index.html` directly (file://)        |
| VITE_API_BASE         | default `''` (use the proxy). Set only if the backend runs on another host |
| VITE_USE_MOCK         | `'true'` forces mock mode (reads frontend/public/mock/)        |

| Command          | Does                                                               |
|------------------|--------------------------------------------------------------------|
| `make setup`     | create .venv, pip install, npm install                             |
| `make network`   | `python backend/generate_bangalore_dma.py` (only with team approval after H3) |
| `make backend`   | `uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload`     |
| `make frontend`  | `cd frontend && npm run dev -- --host`                             |
| `make test`      | `pytest -q backend/tests tests`                                    |
| `make smoke`     | `scripts/smoke_test.sh` against the running backend                |
| `make presets`   | rebuild data_utils/presets.json from the committed .inp            |
| `make fixtures`  | synthetic mock fixtures (only before real snapshots exist)         |
| `make snapshot`  | overwrite mock fixtures with REAL API responses (seed=7)           |

---------------------------------------------------------------------------------------------------
## 5. Naming and ID conventions
- All IDs are STRINGS. Never parse an ID as a number (the Net3 fallback uses IDs like "10", "15", "601").
- Kengeri network: junctions `J1..JN`, pipes `P1..PM`, feeder pipe `P0`, reservoirs `R1..`, tanks `T1..`.
- Never infer a node's type from its ID prefix. Use the `type` field (the Net3 fallback has no prefixes).
- Sensor labels `S1..S5` are UI-only. S1 = sensor_ids[0], and so on. `sensor_readings` is in the same order as `sensor_ids`.
- JSON keys: snake_case. The frontend uses API objects AS-IS (never convert keys to camelCase).
- Units live in the key suffix: `_m`, `_mm`, `_m3h`, `_lps`, `_lpm`, `_cm2`, `_pct`, `_ms`.
- Preset ids: snake_case (`kengeri_mainline_rupture`, `residential_lane_seep`, `joint_crack_junction`).
- Endpoints: lowercase kebab-case under `/api`, NO trailing slash (`/api/simulate-leak`, never
  `/api/simulate-leak/`, because the redirect breaks POST through the Vite proxy).
- `road_class` enum (normalized): `primary` (OSM trunk/primary), `secondary` (secondary/tertiary),
  `residential` (everything else), `feeder` (reservoir feeder pipe).
- Branches: `feat/backend`, `feat/utils`, `feat/frontend`, `feat/pitch`. Commit prefix: `[backend]`, `[utils]`,
  `[frontend]`, `[pitch]`, `[sot]`, e.g. `[backend] add stage-2 verification`.

---------------------------------------------------------------------------------------------------
## 6. Units, coordinates and constants
| Quantity            | API / UI unit         | Inside WNTR      | Conversion                         |
|---------------------|-----------------------|------------------|------------------------------------|
| Leak hole area      | cm² (`_cm2`)          | m²               | m² = cm² / 10000                   |
| Pipe diameter       | mm (`_mm`)            | m                | mm = m × 1000                      |
| Pipe flow           | m³/h (`_m3h`)         | m³/s             | m³/h = m³/s × 3600                 |
| Base demand         | L/s (`_lps`)          | m³/s             | L/s = m³/s × 1000                  |
| Leak / loss rate    | L/min (`_lpm`)        | m³/s             | LPM = m³/s × 60000                 |
| Pressure            | m of head, gauge (`_m`) | m              | -                                  |
| Elevation, length   | m                     | m                | -                                  |
| Time                | ms (`_ms`)            | s                | -                                  |
| Confidence          | **0–100** (`_pct`)    | -                | e.g. 94.2                          |
| Heatmap / leak_probability | **0–1** fraction | -              | e.g. 0.942. NEVER mix with _pct    |

COORDINATES: The API always sends `lat` and `lng` as separate fields. WNTR stores coordinates as `(x=lng, y=lat)`.
Leaflet wants `[lat, lng]`. Swapping them puts the map in Somalia, so check this first if the map looks empty.

| Constant                  | Value     | Constant                 | Value              |
|---------------------------|-----------|--------------------------|--------------------|
| TANKER_LITRES             | 6000      | NUM_SENSORS              | 5                  |
| LPCD (BWSSB norm)         | 135       | MIN_HOP_SEPARATION       | 3                  |
| PERSONS_PER_HOUSEHOLD     | 4.5       | TOP_K_CANDIDATES         | 5                  |
| DISCHARGE_COEFF (Cd)      | 0.75      | HEATMAP_EXPONENT         | 8                  |
| REF_LEAK_CM2              | 25.0      | LEAK_AREA_RANGE_CM2      | 0.5 – 100          |
| REQUIRED_PRESSURE_M       | 15.0      | NOISE_RANGE_PCT          | 0 – 10             |
| MINIMUM_PRESSURE_M        | 2.0       | DEFAULT area / noise     | 25.0 / 1.5         |
| PRESSURE_EXPONENT         | 0.5       | MAP CENTER               | 12.9081, 77.4852   |
| SIM DURATION              | 0 (steady state) | OSM DIST          | 600 m              |
| SNAPSHOT SEED             | 7         | GENERATOR SEED           | 42                 |

Formulas (exact):
- tankers_lost_per_day = round(lpm × 1440 / 6000, 1)
- households (fallback) = clamp(round(base_demand_L_per_day / (135 × 4.5)), 1, 500)
- noise sigma = (noise_pct/100) × max(mean(|Δp_true|), 0.05 m), drawn per sensor with numpy default_rng(seed)
- Rounding: floats 2 decimals; lat/lng 6 decimals; confidence_pct 1 decimal

Physics invariants: WNTRSimulator only (EpanetSimulator ignores leaks) · demand_model 'PDD' · duration 0 ·
leaks via add_leak(wn, area=m², discharge_coeff=0.75, start_time=0) · candidates and sensors only from
wn.junction_name_list · fresh or restored model for every simulation (no leaked state between requests).

---------------------------------------------------------------------------------------------------
## 7. API CONTRACT v1.0 (canonical)
General: JSON only · CORS allow * · no auth · no trailing slashes · no nulls except `seed` · every list is
always present (it may be empty) · response key order doesn't matter · p95 latency < 1000 ms.

Types (TypeScript notation, language-neutral):

  type NodeType = "junction" | "reservoir" | "tank";
  type PipeStatus = "OPEN" | "CLOSED";
  type RoadClass = "primary" | "secondary" | "residential" | "feeder";

  // GET /api/health
  interface HealthResponse { status: "ok"; network_name: string; num_junctions: number; num_pipes: number;
                             sensor_ids: string[ /*len 5*/ ]; matrix_ready: boolean; }

  // GET /api/network
  interface NetworkNode { id: string; type: NodeType; lat: number; lng: number; elevation_m: number;
                          base_pressure_m: number;   // reservoirs/tanks: 0
                          base_demand_lps: number;   // reservoirs/tanks: 0
                          households: number;        // int. reservoirs/tanks: 0
                          is_sensor: boolean; leak_probability: number /*0..1, always 0 here*/; }
  interface NetworkPipe { id: string; source: string; target: string; diameter_mm: number; length_m: number;
                          flow_m3h: number /*signed: + = source→target*/; status: PipeStatus; road_class: RoadClass; }
  interface NetworkResponse { network_name: string; center: { lat: number; lng: number };
                              sensor_ids: string[]; nodes: NetworkNode[]; pipes: NetworkPipe[]; }

  // GET /api/presets
  interface Preset { id: string; label: string; description: string; target_node_id: string;
                     leak_area_cm2: number /*0.5..100*/; noise_pct: number /*0..10*/; }
  interface PresetsResponse { presets: Preset[] /*exactly 3 in the demo*/; }

  // POST /api/simulate-leak
  interface LeakSimulationRequest { target_node_id: string; leak_area_cm2: number /*0.5..100, default 25*/;
                                    noise_pct: number /*0..10, default 1.5*/; seed?: number | null; }
  interface CandidateNode { node_id: string; confidence_pct: number /*0..100*/; estimated_loss_lpm: number;
                            tankers_lost_per_day: number; estimated_leak_area_cm2: number; }
  interface SensorReading { sensor_id: string; baseline_m: number; observed_m: number;
                            drop_m: number /*= baseline_m − observed_m, may be slightly negative with noise*/; }
  interface LeakSimulationResponse {
    injected_node_id: string; injected_leak_area_cm2: number; actual_loss_lpm: number;
    predicted_top_candidates: CandidateNode[]; // 1..5, index 0 = best
    sensor_readings: SensorReading[];          // exactly 5, same order as sensor_ids
    node_heatmap: Record<string, number>;      // keys = EVERY junction id (no reservoirs/tanks), values 0..1, max = 1
    recommended_valves_to_close: string[];     // pipe ids, sorted
    isolated_node_ids: string[];               // junctions cut off from every source (includes the top candidate)
    affected_households_after_isolation: number; // int
    localization_error_m: number;              // haversine(injected, top candidate)
    compute_time_ms: number;
  }

  // POST /api/isolate (OPTIONAL stretch. The frontend must work when this returns 404)
  interface IsolationRequest { valve_ids: string[]; target_node_id: string; leak_area_cm2: number; }
  interface IsolationResponse { node_pressures: Record<string, number>; min_pressure_m: number;
                                nodes_without_supply: string[]; }

Errors (the frontend must handle BOTH shapes):
  404 → { "detail": "Unknown junction id: X" }                     // detail is a STRING
  422 → { "detail": [ { "loc": [...], "msg": "...", "type": "..." } ] }  // FastAPI default, detail is an ARRAY
  500 → { "detail": "Simulation failed: <reason>" }                // the backend catches solver errors

Canonical example: POST /api/simulate-leak
  request : {"target_node_id":"J15","leak_area_cm2":25.0,"noise_pct":1.5,"seed":null}
  response: {"injected_node_id":"J15","injected_leak_area_cm2":25.0,"actual_loss_lpm":482.1,
             "predicted_top_candidates":[{"node_id":"J15","confidence_pct":94.2,"estimated_loss_lpm":480.5,
               "tankers_lost_per_day":115.3,"estimated_leak_area_cm2":24.1}],
             "sensor_readings":[{"sensor_id":"J12","baseline_m":42.5,"observed_m":36.8,"drop_m":5.7}],
             "node_heatmap":{"J1":0.12,"J15":1.0},"recommended_valves_to_close":["P14","P19"],
             "isolated_node_ids":["J15"],"affected_households_after_isolation":42,
             "localization_error_m":0.0,"compute_time_ms":312.4}
  (The example is shortened. Real responses have 5 sensor_readings and every junction in node_heatmap.)

Implementations of this contract, all of which must agree with it:
  data_utils/schemas.py (Pydantic v2 code mirror) · backend/main.py (response_model=...) ·
  frontend/src/api/client.js (consumer) · frontend/public/mock/*.json (fixtures) · tests/test_contract.py (enforcer)

---------------------------------------------------------------------------------------------------
## 8. Mock fixtures (frontend/public/mock/)
| Endpoint                    | Fixture file                                            |
|-----------------------------|---------------------------------------------------------|
| GET  /api/health            | health.json                                             |
| GET  /api/network           | network.json                                            |
| GET  /api/presets           | presets.json                                            |
| POST /api/simulate-leak     | simulate_leak_<preset_id>.json, else simulate_leak_default.json |
- Phase A (H0–H12): synthetic fixtures from `make fixtures`. IDs are FAKE, so never copy them into presets or code.
- Phase B (H12 onward): `make snapshot` replaces them with REAL responses. From then on, fixtures must match the
  committed .inp. Regenerating the network means re-snapshotting.
- Every fixture must validate against data_utils/schemas.py.

---------------------------------------------------------------------------------------------------
## 9. Generated artifacts and what gets committed
| Artifact                              | Committed? | Rule                                                   |
|---------------------------------------|------------|--------------------------------------------------------|
| backend/bangalore_kengeri.inp         | YES        | Generated ONCE at ~H3. OSM data changes over time and regenerating renames IDs. |
| backend/network_meta.json             | YES        | Always committed together with the .inp                |
| backend/sensitivity_matrix.npy/.json  | NO         | Cache. Rebuilt automatically when the .inp hash changes |
| backend/benchmark_report.md           | YES        | The ONLY allowed source of accuracy numbers            |
| data_utils/presets.json               | YES        | Must reference IDs that exist in the committed .inp    |
| frontend/public/mock/*.json           | YES        | See §8                                                 |
| frontend/screenshots/*.png            | YES        | Used by the deck                                       |
| .venv/, venv/, node_modules/, dist/, __pycache__/, .env | NO | In .gitignore                                |

NETWORK FREEZE: after CP1 (H9) nobody runs `make network` without the whole team agreeing. If it does happen,
run this exact sequence: make network → commit .inp + meta → make presets → restart backend → make snapshot →
post "NETWORK REGENERATED" in chat.

---------------------------------------------------------------------------------------------------
## 10. Git workflow
- `main` must always run. Only Hardhik merges into `main`. Never force-push `main`.
- **Every change reaches `main` through a pull request. Nobody (human or agent) pushes to `main` directly.**
  - One PR = one logical change, kept small (aim for under ~400 changed lines, generated files excluded).
    Stack follow-up PRs on top of an unmerged one rather than growing it.
  - Branch from the latest `main` (or from the PR you stack on). Names: `feat/<area>-<topic>`, `fix/<area>-<topic>`,
    `docs/<topic>`; agent branches may use the name their platform assigns.
  - Open the PR as a draft while working, then mark it **Ready for review**.
  - PR description: what changed, why, how it was verified (exact commands + results), what was NOT verified, and any
    deviation from this file. Run your role's §12 checks before marking it ready.
  - Hardhik reviews and merges (squash or merge commit, never a force-push to `main`).
- Work on your branch. `git pull --rebase origin main` at least every 2 hours and before every push.
- Small, frequent commits with the prefixes from §5. One logical change per commit.
- Merge conflicts: the owner of the folder (§2) decides. Never "accept both" in JSON or lockfiles. Regenerate instead.
- Lockfiles: only Badri commits frontend/package-lock.json. Only Gangu edits requirements.txt.
- Tags: `cp1-integration` (H9), `cp2-freeze` (H13), `v1.0-demo` (H22 code freeze).
- After H22: no commits except a fix approved by Hardhik for a demo-breaking bug.

---------------------------------------------------------------------------------------------------
## 11. Contract change protocol
1. Proposer writes a request in HANDOFF.md: field/endpoint, reason, breaking or additive.
2. Hardhik approves or rejects in chat.
3. If approved, apply the changes in this order: SOURCE_OF_TRUTH.md (bump version + changelog) → data_utils/schemas.py
   (Gangu) → backend (Hardhik) → fixtures (`make snapshot`, Gangu) → frontend (Badri) → `make test && make smoke`.
4. Additive changes (a new OPTIONAL field) bump the minor version (1.1.0). Breaking changes bump the major version and are
   FORBIDDEN after CP2 (H13).
5. Agents must NEVER change the contract on their own. If an agent thinks it has to, it stops and reports.

---------------------------------------------------------------------------------------------------
## 12. Definition of done (run before every push)
- Hardhik: `make test` green · `make smoke` green · cold boot < 60 s, warm < 5 s · no EpanetSimulator in the leak
  path · `grep -n "duration" backend/` shows 0 only.
- Gangu: all fixtures validate · presets reference real IDs · `make setup` works on a clean clone · tests/test_contract.py green.
- Badri: `npm run build` passes · zero console errors · works with VITE_USE_MOCK=true AND against the live API ·
  handles 404, 422-array and offline · checked at 1366×768 and 1920×1080.
- Jasmine: deck opens offline via file:// · no console errors · every number either sourced in sources.md or
  tagged [VERIFY] · accuracy only from benchmark_report.md.

---------------------------------------------------------------------------------------------------
## 13. Communication
- HANDOFF.md (repo root, append-only) entry format:
  `[H07:30] FROM gangu TO badri: real presets.json committed (3 ids: J15, J88, J41). ACTION: none.`
- Status ping every 2 hours in the team chat: `done / doing / blocked-by`.
- A blocker that lasts more than 20 minutes gets escalated to Hardhik immediately. Don't burn time silently.
- Checkpoints: CP1 H9 (live integration) · CP2 H13 (benchmark → deck, API frozen) · CP3 H18 (dress rehearsal).

---------------------------------------------------------------------------------------------------
## 14. Known pitfalls register (check here first when something breaks)
| # | Symptom                                       | Cause                                         | Fix |
|---|-----------------------------------------------|-----------------------------------------------|-----|
| 1 | Leak has no effect, loss = 0                  | EpanetSimulator used, or start_time not set   | WNTRSimulator + start_time=0 |
| 2 | Solver diverges / absurd loss                 | cm² passed to WNTR as m²                      | divide by 10000 |
| 3 | Each sim takes seconds                        | duration not 0                                | wn.options.time.duration = 0 |
| 4 | Cosine NaN / divide-by-zero                   | reservoir/tank in candidates or zero vector   | junction_name_list only, guard norms |
| 5 | Results drift between requests                | model mutated, leak not removed               | deepcopy or remove_leak + reset_initial_values under a lock |
| 6 | Map empty / markers in the ocean              | lat/lng swapped                               | §6 coordinates rule |
| 7 | Presets 404 on a real backend                 | presets built from mock or old-network IDs    | `make presets` after the .inp is committed |
| 8 | POST returns 307 / fails through the proxy    | trailing slash in the URL                     | no trailing slashes |
| 9 | Frontend shows "[object Object]" on error     | 422 detail is an array                        | handle string AND array detail |
|10 | Confidence shows 9420% or 0.9%                | _pct vs fraction mixed up                     | §6: confidence 0–100, heatmap 0–1 |
|11 | `ModuleNotFoundError: backend`                | ran from inside backend/ or missing __init__  | run from the repo root, absolute imports |
|12 | CORS error in the browser                     | middleware missing or wrong port              | CORSMiddleware allow *, use the Vite proxy |
|13 | IDs changed overnight                         | someone re-ran `make network`                 | §9 network freeze |
|14 | Node ID "15" treated as number 15             | parseInt / numeric keys                       | IDs are always strings |
|15 | Lockfile merge hell                           | multiple people ran npm install with changes  | only Badri touches package*.json |
|16 | Map blank at the venue                        | no internet for tiles / CDN fonts             | basemap fallback + bundled fonts, offline deck |
|17 | Slides show fake accuracy                     | dossier "94.2%" quoted                        | benchmark_report.md only |
|18 | OSMnx AttributeError                          | v1 vs v2 API differences                      | use version-tolerant calls (see Hardhik's plan) |
|19 | Disconnected-network crash                    | OSM fragments                                 | largest connected component only |

---------------------------------------------------------------------------------------------------
## 15. Changelog
| Version | Time | Author  | Change                          |
|---------|------|---------|---------------------------------|
| 1.0.0   | H0   | Hardhik | Initial frozen contract and rules |
| 1.0.1   | backend MVP | Hardhik | Non-breaking. §1: candidate screening is a size-aware fingerprint fit (cosine-only screening scored 27.5% top-1 at 0% noise in the benchmark); no API field changed. `seed` must be ≥ 0 (negative seeds were a 500, now a 422) |
````

**`AGENTS.md`** (repo root; many coding agents read this automatically):

```markdown
# Agent instructions
Before doing anything, read `SOURCE_OF_TRUTH.md` in this folder. It overrides any other prompt.
Only edit the folders your human owner owns (SOURCE_OF_TRUTH.md §2). Never change API fields, IDs, units,
ports or paths. Run your role's checks from §12 before saying a task is done. If something conflicts, stop and ask.
```

**`.gitattributes`** (repo root):

```text
* text=auto eol=lf
*.png binary
*.jpg binary
*.npy binary
*.mp4 binary
*.woff2 binary
```
