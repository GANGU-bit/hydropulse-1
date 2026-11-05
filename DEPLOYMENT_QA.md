# HydroPulse — Manual QA & Staging Verification Runbook

## 1. Deployment Overview & Verified URLs

| Component | Target URL / Value | Status |
|:---|:---|:---:|
| **Frontend Production URL** | [https://hydropulse-iota.vercel.app](https://hydropulse-iota.vercel.app) | **LIVE** |
| **Backend Public URL (Tunnel)** | [https://overall-union-bloom-essential.trycloudflare.com](https://overall-union-bloom-essential.trycloudflare.com) | **LIVE** |
| **Health Probe** | [https://overall-union-bloom-essential.trycloudflare.com/api/health](https://overall-union-bloom-essential.trycloudflare.com/api/health) | **HTTP 200 OK** |
| **Readiness Probe** | [https://overall-union-bloom-essential.trycloudflare.com/api/ready](https://overall-union-bloom-essential.trycloudflare.com/api/ready) | **HTTP 200 OK** |
| **Interactive Swagger API Docs** | [https://overall-union-bloom-essential.trycloudflare.com/docs](https://overall-union-bloom-essential.trycloudflare.com/docs) | **HTTP 200 OK** |
| **Deployed Git Commit** | `fc80cd7f` (branch `feat/backend-deployment-readiness`) | **VERIFIED** |
| **Host Environment** | AWS EC2 `t3.micro` (`i-06789c5dde3b46300`), Ubuntu 22.04.5 LTS x86_64 | **RUNNING** |
| **Runtime & Process Model** | Python 3.11.18, Uvicorn single-worker bound to loopback `127.0.0.1:8000` | **VERIFIED** |
| **Process Manager** | Systemd unit `hydropulse.service` running as non-root system user `hydropulse` | **ACTIVE** |
| **Public Ingress** | Cloudflare Quick Tunnel service `hydropulse-tunnel.service` to `127.0.0.1:8000` | **ACTIVE** |

---

## 2. Security Boundaries & Configuration

1. **Host Binding**: Uvicorn binds strictly to `127.0.0.1:8000`. Direct public internet access to port 8000 is blocked by EC2 Security Group `sg-063b4f525adec76dd` and loopback interface isolation.
2. **CORS Origins**: Strict whitelist configured in `/etc/hydropulse/hydropulse.env`:
   - `https://hydropulse-iota.vercel.app`
   - `https://hydropulse.vercel.app`
   - `https://overall-union-bloom-essential.trycloudflare.com`
3. **Administrative Protection**: Mutating endpoints (`/api/iot/ingest`, `/api/iot/auto-valve`, `/api/network/import/commit`, `/api/network/reset`, `/api/network/build/commit`, `/api/ai/apply-suggestion`) require `X-Admin-Token` or `Authorization: Bearer <token>`. Public simulation and inspection endpoints remain open.
4. **State Persistence**: Durable state and precomputed sensitivity matrices are stored at `/var/lib/hydropulse` (mode `0750`, owned by `hydropulse:hydropulse`).

---

## 3. Automated Test Verification Summary

All verification suites were executed against the **live public Cloudflare Tunnel URL over the internet**:

### A. Contract Tests (`tests/test_contract.py`)
- **Execution Command**:
  ```bash
  BASE="https://overall-union-bloom-essential.trycloudflare.com" ADMIN_TOKEN="<token>" pytest tests/test_contract.py
  ```
- **Result**: **9 passed in 9.94s (100% PASS)**
- **Verified Schemas & Endpoints**:
  1. `test_health_endpoint` (validates `HealthResponse`, `network_revision`, `source`, `can_rollback`)
  2. `test_network_endpoint` (validates `NetworkResponse`, `center`, nodes, pipes, coordinates)
  3. `test_presets_endpoint` (validates 3 canonical presets)
  4. `test_simulate_leak_preset_endpoints` (validates `predicted_top_candidates`, `node_heatmap`, valve closures)
  5. `test_simulate_leak_error_cases` (verifies 404 string detail and 422 validation array)
  6. `test_v1_1_what_if_contract_and_isolation` (verifies ephemeral What-If scenario sandbox)
  7. `test_v1_1_builder_and_intellisense_contracts` (verifies diameter sizing and topology validation)
  8. `test_v1_1_ai_advisor_suggestions_contract` (verifies 5 deterministic AI recommendations and benchmark)
  9. `test_v1_1_iot_and_pipe_condition_contracts` (verifies IoT buffer, pipe condition report, auto-valve safety gate)

### B. End-to-End Smoke Test Suite (`scripts/smoke_test.sh`)
- **Execution Command**:
  ```bash
  BASE="https://overall-union-bloom-essential.trycloudflare.com" ADMIN_TOKEN="<token>" ./scripts/smoke_test.sh
  ```
- **Result**: **11/11 Gates PASSED (100% PASS)**
  - Gate 1: `GET /api/health` → PASSED (`rev=rev-1-08c49b29`)
  - Gate 2: `GET /api/network` → PASSED (`name=kengeri_synthetic nodes=130 pipes=203 sensors=5`)
  - Gate 3: `GET /api/presets` → PASSED (3 presets loaded)
  - Gate 4: `POST /api/simulate-leak` for all 3 presets → PASSED:
    * `kengeri_mainline_rupture`: Target `J1` → Predicted `J12` (100% conf, 126.8m error, 1,126 tankers/d, 649 ms)
    * `residential_lane_seep`: Target `J124` → Predicted `J56` (100% conf, 226.5m error, 267 tankers/d, 495 ms)
    * `joint_crack_junction`: Target `J68` → Predicted `J56` (100% conf, 115.6m error, 679 tankers/d, 344 ms)
  - Gate 5: Invalid node error → PASSED (`404 Not Found`)
  - Gate 6: Invalid area error → PASSED (`422 Unprocessable Entity`)
  - Gate 7: `POST /api/what-if` → PASSED (`rev=rev-1-08c49b29`, baseline and scenario comparison)
  - Gate 8: `GET /api/network/presets` & `POST /api/network/intellisense` → PASSED (`recommended_diameter=110mm`)
  - Gate 9: `GET /api/iot/state` & `GET /api/pipes/condition` → PASSED (203 pipes, RUL transparency honest)
  - Gate 10: `POST /api/iot/auto-valve` safety gate → PASSED (unconfirmed close safely refused)
  - Gate 11: `GET /api/ai/suggestions` → PASSED (5 recommendations generated)

---

## 4. Manual Tester Step-by-Step Instructions

### Test Case 1: SCADA Dashboard & Leak Simulation
1. Open [https://hydropulse-iota.vercel.app](https://hydropulse-iota.vercel.app) in Google Chrome or Firefox.
2. Verify header shows **Status: ONLINE** and network name **kengeri_synthetic** (rev `rev-1-08c49b29`).
3. Under **Presets**, click **"Kengeri Mainline Rupture"**.
   - **Expected**: Crimson heatmap appears on map converging on candidate nodes.
   - **Expected**: Incident Panel reports flow loss in Litres/Minute and tankers/day (~1,000+ tankers/day).
   - **Expected**: Recommended isolation valves appear with amber highlight lines.
   - **Expected**: Pressure charts display baseline vs observed sensor readings for $S_1$ to $S_5$.
4. Toggle **"Judge Mode"**:
   - Click an arbitrary junction node on the map.
   - Click **"Run Blind Test"**.
   - Verify sensor pressure drops are shown while candidate location is concealed.
   - Click **"Reveal Truth"** and verify ground-truth marker and localization error in meters.

### Test Case 2: 2D Network Studio Schematic Editor
1. In the header, click **"Network Studio"** or press keyboard shortcut **`N`**.
2. Drag and drop a new **Junction** onto the canvas.
3. Click the edge connection handle and connect it to an existing junction with a pipe.
4. Open the **Properties Panel** and edit pipe diameter ($150\text{ mm}$) and length ($100\text{ m}$).
5. Click **"Validate Topology"** to run the undirected BFS audit.

### Test Case 3: Municipal ROI Financial Calculator
1. In the bottom navigation panel, navigate to **Municipal ₹ ROI**.
2. Adjust the sliders:
   - District Supply: $100\text{ MLD}$
   - Current NRW: $35\%$
   - Recoverable Leakage: $20\%$
   - Utility Production Cost: $₹45\text{ / kL}$
3. Verify live calculation displays:
   - Annual Municipal Savings: **~₹5.74 Crore / year**
   - Daily Tankers Saved: **~583 Tankers / day**

---

## 5. Known Limitations & Operational Notes
1. **Cloudflare Quick Tunnel**:
   - The Quick Tunnel domain (`https://*.trycloudflare.com`) is an ephemeral tunnel for staging and evaluation. If the `hydropulse-tunnel.service` is restarted, a new URL may be assigned.
   - For long-term production, a named Cloudflare Tunnel with custom domain routing (e.g. `api.hydropulse.org`) should be configured.
2. **Single Worker Concurrency**:
   - The backend runs as a single Uvicorn worker process because EPANET WNTR simulation models and network revision states are maintained process-locally. Do not increase worker count beyond 1.
3. **OpenRouter AI Enrichment**:
   - OpenRouter integration is optional and defaults to `:free` models (`meta-llama/llama-3.3-70b-instruct:free`). If no key is set or the API is unavailable, the backend automatically uses deterministic physics-backed rationales with zero downtime.
