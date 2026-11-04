# Plan 3: BADRI (Frontend and Design, ~15%)

**Mission:** The judges will spend most of their time looking at your screen. Build a dark "BWSSB control-room" SCADA dashboard: a real Kengeri street map with cyan pipes, 5 emerald sentinel sensors, a burst you trigger in one click, a crimson heatmap that converges on the leak, live sensor charts, flashing orange isolation valves, and a tanker-loss counter.

## Owned files
`frontend/**` (except `frontend/public/mock/`, which Gangu writes)

## Phases

| # | Window | Deliverable | Done when |
|---|---|---|---|
| 1 | H0–H1 | Vite + React + Tailwind scaffold, design tokens, layout shell | 4-panel layout renders at 1920×1080 and 1366×768 |
| 2 | H1–H4 | MapCanvas on mock `network.json` | Pipes, sensors and the reservoir draw correctly. Clicking a node selects it |
| 3 | H4–H7 | ControlPanel, PressureCharts, IncidentPanel | Full simulate → result flow on mocks |
| 4 | H7–H9 | API client: real/mock switch, loading/error states | Backend-down banner, auto fallback to mock |
| — | **H9** | **CP1: switch to the live API with Hardhik** | Real Kengeri network renders |
| 5 | H9–H13 | Isolation animation, heatmap polish, judge mode, RoiPanel | The demo storyline works end to end |
| 6 | H13–H18 | Polish: motion, typography, projector test, keyboard shortcuts | Looks great on the venue projector |
| 7 | H18–H24 | Bug fixes only | |

## Handoffs
- **Needs from Gangu:** `frontend/public/mock/*.json` by H2, real snapshots by H12.
- **Needs from Hardhik:** API on `localhost:8000` by H9.
- **Gives Jasmine:** 3 screenshots at H14 (idle map, leak located, valves isolated) for the deck.

---

## 🎯 Goal Prompt (paste into your agent)

````text
ROLE
You are the frontend engineer and product designer for "HydroPulse", a 24-hour hackathon project (Lakshya 2026 @
SJBIT Bengaluru, Track 02 Smart & Sustainable Future). HydroPulse is a software-only digital twin of a Bengaluru
water network (Kengeri ward). A FastAPI backend simulates an underground pipe burst, reads only 5 "sentinel"
pressure sensors, triangulates the leaking junction, and recommends valves to close. You build the interactive
SCADA control-room dashboard that judges will use live on stage. It must look like premium, credible municipal
utility software (think BWSSB control room), not a student project.

GOAL
Inside `hydropulse/frontend/`, build a production-quality React app. You're done only when every ACCEPTANCE CHECK
passes in a real browser.

STACK
Vite + React 18 (JS/JSX) + Tailwind CSS 3 + react-leaflet 4 / leaflet + Recharts + lucide-react +
@fontsource/inter + @fontsource/jetbrains-mono (bundled fonts so it works OFFLINE. No Google Fonts CDN).
vite.config.js: dev server proxy '/api' → http://localhost:8000. Env vars: VITE_API_BASE (default ''),
VITE_USE_MOCK ('true' forces mock mode). Import 'leaflet/dist/leaflet.css'.

DESIGN SYSTEM (put these in tailwind.config.js as tokens)
- Background #070B14, panels #0D1424 with 1px border #1E2A44 and subtle inner glow, text #E2E8F0, muted #64748B.
- Pipes: cyan #22D3EE. Weight by diameter: 400 mm → 5px, 250 mm → 3px, ≤150 mm → 1.5px. Opacity 0.75.
- Sentinel sensors: emerald #10B981 core with an animated pulsing ring and a label chip "S1..S5".
- Reservoir: blue #3B82F6 square marker with a droplet icon.
- Leak: crimson #EF4444 with a radar-pulse animation. Valves to close: amber #F59E0B, flashing dashed polylines.
- Heatmap ramp for leak_probability: transparent → #1E3A8A → #F59E0B → #EF4444, drawn as CircleMarkers (radius 4–14 px
  scaled by value). Don't use the leaflet.heat plugin.
- Numbers are always JetBrains Mono with tabular-nums. UI text is Inter. Corner radius 10px. 8-pt spacing grid.
- Motion: 150–300 ms ease-out transitions, no bouncy gimmicks. Respect prefers-reduced-motion.

LAYOUT (single screen, no scrolling at 1366×768 or larger)
┌ Header: HydroPulse logo (droplet+pulse icon) · "Kengeri DMA · BWSSB Digital Twin" · system status pill
│ (LIVE API / MOCK MODE / OFFLINE) · junction/pipe/sensor counts · live clock (IST)
├ Left (320px): ControlPanel        ├ Center: MapCanvas (flex)        ├ Right (360px): IncidentPanel
└ Bottom drawer (collapsible, 220px): PressureCharts

COMPONENTS (src/components/)
1. MapCanvas.jsx
   - Center on network.center, zoom 16. Basemap: CARTO dark (https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png,
     with attribution). Add a "Basemap: On/Off" toggle. If tiles fail to load (tileerror), switch automatically to
     a no-tile dark background with a faint grid so the demo still works without internet.
   - Draw pipes as Polylines (source → target coordinates). Hover tooltip: id, diameter, length, flow, road class.
   - Draw junctions as small CircleMarkers. Click selects the target node (white ring). Tooltip: id, pressure,
     elevation, households.
   - Sensors and reservoir sit above the junctions. After a result: the heatmap layer, a crimson pulsing marker on the
     top candidate (label "LEAK · J15 · 94.2%"), numbered rank badges on candidates 2–5, and a dashed white
     ring on the true injected node (shown only after reveal in judge mode). Isolation state: recommended valve
     pipes flash amber, isolated nodes are greyed out, and the rest of the network stays cyan ("still pressurized").
   - "Scanning" effect while a request is in flight: a translucent radar sweep over the map, plus the sensors pulse
     faster. Keep it on screen for at least 900 ms for stage drama, but always show the real result, never fake data.
   - Legend in the bottom-left corner.
2. ControlPanel.jsx
   - Preset cards from GET /api/presets (icon, label, description, area, noise). One click loads the preset and runs it.
   - Target node: shows the selected node id (from a map click) or a searchable dropdown of junction ids.
   - Slider "Leak hole area" 0.5–100 cm² (default 25). Show live helper text such as "≈ coin-sized crack" or
     "≈ mainline blowout".
   - Slider "Sensor noise" 0–10 % (default 1.5).
   - Primary button "Simulate Burst" (crimson). Secondary "Isolate Valves" (amber, enabled after a result).
     "Reset" (ghost button).
   - Toggle "Judge Mode (blind)": the selected target is hidden on the map, so a judge can secretly pick a node
     and the audience sees HydroPulse find it. Then a "Reveal Truth" button shows the injected node and the
     localization error in metres.
3. IncidentPanel.jsx
   - Idle state: network health summary (min/avg pressure, total households, 5 sensors online, all green).
   - Result state: big headline "LEAK PINPOINTED · J15" with confidence %. A count-up for loss (LPM) and
     "BWSSB tankers lost / day" (6,000 L each) with a tanker icon. Estimated hole size. Compute time in ms.
     Localization error once revealed. A ranked list of the top-5 candidates with confidence bars, where clicking
     one flies the map to it. An isolation card listing the valves to close, households affected, and a "% of ward
     still supplied" ring.
4. PressureCharts.jsx (Recharts)
   - Grouped bar chart: baseline vs observed pressure for S1–S5, with the drop labelled on each bar.
   - A small "drop signature" bar chart (drop_m per sensor) that visualizes the fingerprint the cosine matcher uses.
   - Animate bars when a new result arrives. Use the dark theme on axes and tooltips.
5. RoiPanel.jsx (a compact card or modal opened from the header)
   - Inputs: leaks detected per month (default 20), average loss LPM (prefilled from the last result), days saved
     per leak (default 12), production cost ₹/kL (default 45, labelled "editable assumption").
   - Outputs: litres saved/year, tankers saved/year, ₹ saved/year (lakh/crore formatting via Intl en-IN).
     Formula: litres = leaks × 12 × lpm × 1440 × days_saved.
6. Header.jsx, StatusBanner.jsx (backend unreachable → amber banner "Backend offline: running on recorded
   data" with a retry button).

DATA LAYER (src/api/client.js + src/hooks/useHydroPulse.js)
- getHealth, getNetwork, getPresets, simulateLeak(body), isolate(body) [optional endpoint, so hide the
  "re-simulate isolation" feature on 404].
- Mock mode: GET /mock/network.json, /mock/presets.json, /mock/health.json. For a simulate call, load
  /mock/simulate_leak_<preset_id>.json when a preset is active, else /mock/simulate_leak_default.json. If those
  files are missing, generate a plausible mock in JS (peak at the selected node, decay by distance).
- Auto mode: try the live API with a 4 s timeout. If it fails, switch to mock mode and show the banner.
- State machine: idle → simulating → located → isolated → (reset) idle. Store lastRequest and lastResult.
- Keyboard shortcuts for the stage: 1/2/3 run presets, Space simulates, I isolates, R resets, J toggles judge mode,
  V reveals, B toggles the basemap, F goes fullscreen. Show a "?" overlay listing them.

API CONTRACT v1.0 (the backend returns exactly this)
GET /api/health → {"status":"ok","network_name":"bangalore_kengeri","num_junctions":184,"num_pipes":231,
                   "sensor_ids":["J12","J40","J77","J103","J150"],"matrix_ready":true}
GET /api/network →
{ "network_name":"bangalore_kengeri", "center":{"lat":12.9081,"lng":77.4852}, "sensor_ids":[...5],
  "nodes":[{"id":"J1","type":"junction","lat":12.90812,"lng":77.48521,"elevation_m":815.2,"base_pressure_m":42.5,
            "base_demand_lps":0.35,"households":50,"is_sensor":true,"leak_probability":0.0}],   // type: junction|reservoir|tank
  "pipes":[{"id":"P1","source":"J1","target":"J2","diameter_mm":400.0,"length_m":120.5,"flow_m3h":142.8,
            "status":"OPEN","road_class":"primary"}] }
GET /api/presets → {"presets":[{"id":"kengeri_mainline_rupture","label":"Kengeri Mainline Rupture",
                   "description":"...","target_node_id":"J15","leak_area_cm2":45.0,"noise_pct":1.5}]}
POST /api/simulate-leak  body {"target_node_id":"J15","leak_area_cm2":25.0,"noise_pct":1.5,"seed":null} →
{ "injected_node_id":"J15","injected_leak_area_cm2":25.0,"actual_loss_lpm":482.1,
  "predicted_top_candidates":[{"node_id":"J15","confidence_pct":94.2,"estimated_loss_lpm":480.5,
                               "tankers_lost_per_day":115.3,"estimated_leak_area_cm2":24.1}],      // ≤5, best first
  "sensor_readings":[{"sensor_id":"J12","baseline_m":42.5,"observed_m":36.8,"drop_m":5.7}],        // exactly 5
  "node_heatmap":{"J1":0.12,"J15":1.0},                                                           // all junctions 0..1
  "recommended_valves_to_close":["P14","P19"],"isolated_node_ids":["J15"],
  "affected_households_after_isolation":42,"localization_error_m":0.0,"compute_time_ms":312.4 }
Errors: 404 {"detail":"Unknown junction id: X"} and 422 validation, shown as toasts.
Optional POST /api/isolate {valve_ids,target_node_id,leak_area_cm2} → {node_pressures,min_pressure_m,nodes_without_supply}

QUALITY BAR
- No console errors. No layout shift. Every async state has a loading and an error UI.
- Large map performance: memoize layers, and use preferCanvas on the MapContainer for 200+ nodes.
- Accessible: buttons have aria-labels, contrast ≥ 4.5:1, and every control works from the keyboard.
- Clean structure: components/, api/, hooks/, lib/format.js (Indian number formatting, units).
- npm run build must succeed with no warnings that matter.

ACCEPTANCE CHECKS
[ ] VITE_USE_MOCK=true npm run dev → the full flow works with only the mock files (no backend).
[ ] With the backend on :8000 → the status pill shows LIVE API and the real Kengeri network renders on the real streets.
[ ] Preset click → scanning animation → crimson leak marker + heatmap + charts + loss/tankers counters.
[ ] Isolate → valve pipes flash amber, isolated nodes grey, households affected shown.
[ ] Judge mode: a hidden pick, then simulate, then reveal shows the truth ring + error in metres.
[ ] Kill the backend mid-demo → banner appears, mock fallback works, no white screen.
[ ] Basemap off / no internet → map still usable on the dark grid.
[ ] Looks polished at 1920×1080 and 1366×768. Keyboard shortcuts work. npm run build passes.
[ ] Save 3 screenshots (idle, located, isolated) to frontend/screenshots/ for the pitch deck.
Report: what you built, how to run it, screenshots, and known issues.
````
