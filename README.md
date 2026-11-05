Exit code: 0
Wall time: 3.7 seconds
Output:
# HydroPulse ðŸ’§âš¡

**Software-Only Municipal Hydraulic Digital Twin for Pipe Burst Triangulation**
*Lakshya 2026 @ SJBIT Bengaluru Â· Track 02: Smart and Sustainable Future*

---

## 1. Project Overview

HydroPulse is a software-only digital twin of Bengaluru's municipal water distribution network (specifically the Kengeri ward adjacent to SJBIT). Using EPANET network modeling and US EPA WNTR (Pressure-Driven Demand with Torricelli orifice leak physics), HydroPulse continuously monitors pressure differentials across just **5 strategically placed sentinel junction sensors**.

When an underground main bursts:
1. The 5-sensor pressure-drop fingerprint is cross-referenced against a precomputed sensitivity matrix using scale-invariant cosine similarity.
2. A Stage-2 physics verification re-simulates the top 5 candidates with estimated hole areas to eliminate false positives.
3. The system pinpoints the exact leaking junction / city block.
4. Loss rate is converted to liters per minute (LPM) and equivalent BWSSB 6,000L water tankers per day.
5. An optimal valve-isolation plan is immediately computed to cut off the ruptured segment while minimizing disconnected households.

**Zero hardware required.**

---

## 2. Architecture Summary

```
   OpenStreetMap (OSM) Road Network [Kengeri, Bengaluru]
                          â”‚
                          â–¼
            backend/generate_bangalore_dma.py
                          â”‚
                          â–¼
        EPANET Network (.inp) + network_meta.json
                          â”‚
                          â–¼
             WNTR Hydraulic Engine (PDD)
      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
      â–¼                                       â–¼
5 Sentinel Sensors                   Sensitivity Matrix
(Betweenness + Hop Sep)              (N junctions Ã— 5 sensors)
      â”‚                                       â”‚
      â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                          â–¼
             Two-Stage Triangulation
        1. Cosine screening â†’ Top 5 candidates
        2. Re-simulation verification & re-ranking
                          â”‚
                          â–¼
              FastAPI Service (Port 8000)
    /api/health Â· /api/network Â· /api/presets Â· /api/simulate-leak
                          â”‚
                          â–¼
               Vite + React SCADA UI
   Leaflet GIS Map Â· Pressure Gauges Â· Heatmap Â· Isolation Plan
```

---

## 3. Team Ownership Matrix

Per `SOURCE_OF_TRUTH.md`:

| Directory / File | Owner | Responsibility |
| :--- | :--- | :--- |
| `backend/**` | **Hardhik** | WNTR physics engine, triangulation, FastAPI server, benchmarks |
| `data_utils/**`, `scripts/**`, `tests/**`, `Makefile` | **Gangu** | Pydantic schemas, geo-mapper, mock fixtures, smoke tests, presets, repo setup |
| `frontend/**` (except `public/mock/`) | **Badri** | SCADA control-room UI, React-Leaflet map, Recharts telemetry |
| `pitch_and_roi/**` | **Jasmine** | Pitch deck, financial ROI calculator, presentation script, Q&A defense |

---

## 4. Quickstart

### Prerequisites
- Python 3.10 â€“ 3.12 (Python 3.11 recommended)
- Node.js 20 LTS & npm
- Git

### Installation

```bash
# 1. Clone repository
git clone <repo-url>
cd hydropulse

# 2. Run automated setup (creates .venv, installs Python & npm dependencies)
make setup
# OR on Windows PowerShell:
# .\scripts\setup.ps1
```

### Running the Services

```bash
# Terminal 1: Start FastAPI backend (http://localhost:8000, docs at /docs)
make backend
# OR: .\scripts\run_backend.ps1

# Terminal 2: Start Vite frontend dashboard (http://localhost:5173)
make frontend
# OR: .\scripts\run_frontend.ps1
```

---

## 5. API Endpoints (Contract v1.0)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Health check, sensor list, matrix readiness |
| `GET` | `/api/network` | Nodes, pipes, coordinates, base pressures, demands |
| `GET` | `/api/presets` | 3 curated demo presets |
| `POST` | `/api/simulate-leak` | Inject leak, triangulate location, calculate loss & valve isolation |
| `POST` | `/api/isolate` *(stretch)* | Compute remaining pressure after closing valves |

---

## 6. Testing & Demo Insurance

```bash
# Run contract and unit tests
make test

# Run API smoke test against running backend
make smoke

# Regenerate mock fixtures for frontend
make fixtures

# Snapshot live backend responses into mock fixtures for offline demo insurance
make snapshot
```

---

## 7. Troubleshooting

- **CORS Errors**: Always access the UI via `http://localhost:5173` which proxies `/api` requests to backend port 8000.
- **Port Conflicts**: Ensure port 8000 (backend) and 5173 (frontend) are free before launching.
- **Offline Venue Setup**: See `RUN_OF_SHOW.md` for full instructions to run HydroPulse completely offline without internet connection.

