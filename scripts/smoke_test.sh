#!/usr/bin/env bash
set -e

BASE="${BASE:-http://localhost:8000}"
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
AUTH_HEADER=()
if [ -n "$ADMIN_TOKEN" ]; then
    AUTH_HEADER=(-H "X-Admin-Token: $ADMIN_TOKEN")
fi
echo "=================================================="
echo "    HydroPulse API Smoke Test (Target: $BASE)    "
echo "=================================================="

# Check if curl is available
if ! command -v curl &>/dev/null; then
    echo "Error: curl is required for smoke tests."
    exit 1
fi

# 1. Health check
echo -n "[1/11] Testing GET /api/health ... "
HEALTH_RES=$(curl -s -f "$BASE/api/health")
echo "$HEALTH_RES" | python -m json.tool > /dev/null
STATUS=$(echo "$HEALTH_RES" | python -c "import sys, json; print(json.load(sys.stdin).get('status', ''))")
REV=$(echo "$HEALTH_RES" | python -c "import sys, json; print(json.load(sys.stdin).get('network_revision', ''))")
if [ "$STATUS" != "ok" ]; then
    echo "FAILED (status is not ok)"
    exit 1
fi
echo "PASSED (rev=$REV)"

# 2. Network check
echo -n "[2/11] Testing GET /api/network ... "
NET_RES=$(curl -s -f "$BASE/api/network")
echo "$NET_RES" | python -m json.tool > /dev/null
NET_STATS=$(echo "$NET_RES" | python -c "import sys, json; d=json.load(sys.stdin); print(f'name={d.get(\"network_name\")} nodes={len(d.get(\"nodes\",[]))} pipes={len(d.get(\"pipes\",[]))} sensors={len(d.get(\"sensor_ids\",[]))}')")
echo "PASSED ($NET_STATS)"

# 3. Presets check
echo -n "[3/11] Testing GET /api/presets ... "
PRESETS_RES=$(curl -s -f "$BASE/api/presets")
echo "$PRESETS_RES" | python -m json.tool > /dev/null
PRESET_COUNT=$(echo "$PRESETS_RES" | python -c "import sys, json; print(len(json.load(sys.stdin).get('presets', [])))")
if [ "$PRESET_COUNT" -lt 1 ]; then
    echo "FAILED (no presets found)"
    exit 1
fi
echo "PASSED ($PRESET_COUNT presets)"

# 4. Simulate leak for each preset
echo "[4/11] Testing POST /api/simulate-leak for presets:"
python -c "
import sys, json, urllib.request

base = '$BASE'
presets_data = json.loads('''$PRESETS_RES''')
for p in presets_data.get('presets', []):
    pid = p['id']
    target = p['target_node_id']
    area = p['leak_area_cm2']
    noise = p['noise_pct']

    payload = json.dumps({'target_node_id': target, 'leak_area_cm2': area, 'noise_pct': noise, 'seed': 42}).encode()
    req = urllib.request.Request(f'{base}/api/simulate-leak', data=payload, headers={'Content-Type': 'application/json'})

    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode())

    top = res['predicted_top_candidates'][0]
    conf = top['confidence_pct']
    err = res['localization_error_m']
    ms = res['compute_time_ms']
    tankers = top['tankers_lost_per_day']
    print(f'   -> Preset [{pid}]: target={target} | predicted={top[\"node_id\"]} ({conf}%) | err={err}m | tankers={tankers}/d | {ms:.1f}ms')
"

# 5. Bad node id -> expect 404
echo -n "[5/11] Testing POST /api/simulate-leak with invalid node (expect 404) ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/simulate-leak" \
    -H "Content-Type: application/json" \
    -d '{"target_node_id":"NONEXISTENT_NODE_999","leak_area_cm2":25.0,"noise_pct":1.5}')
if [ "$HTTP_CODE" -eq 404 ]; then
    echo "PASSED (received 404)"
else
    echo "FAILED (expected 404, got $HTTP_CODE)"
    exit 1
fi

# 6. Invalid area -> expect 422
echo -n "[6/11] Testing POST /api/simulate-leak with leak_area_cm2=500 (expect 422) ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/simulate-leak" \
    -H "Content-Type: application/json" \
    -d '{"target_node_id":"J1","leak_area_cm2":500.0,"noise_pct":1.5}')
if [ "$HTTP_CODE" -eq 422 ]; then
    echo "PASSED (received 422)"
else
    echo "FAILED (expected 422, got $HTTP_CODE)"
    exit 1
fi

# 7. What-If simulation (v1.1)
echo -n "[7/11] Testing POST /api/what-if (v1.1 scenario sandbox) ... "
WHATIF_RES=$(curl -s -f -X POST "$BASE/api/what-if" \
    -H "Content-Type: application/json" \
    -d '{"scenario_name":"Smoke What-If","demand_multiplier":1.15,"reservoir_head_delta_m":2.0}')
WHATIF_REV=$(echo "$WHATIF_RES" | python -c "import sys, json; d=json.load(sys.stdin); assert 'scenario' in d and 'baseline' in d; print(d.get('network_revision',''))")
echo "PASSED (rev=$WHATIF_REV)"

# 8. Network presets & IntelliSense (v1.1)
echo -n "[8/11] Testing GET /api/network/presets & POST /api/network/intellisense ... "
NPRESETS_RES=$(curl -s -f "$BASE/api/network/presets")
echo "$NPRESETS_RES" | python -c "import sys, json; d=json.load(sys.stdin); assert len(d.get('presets',[])) >= 3"
ISENSE_RES=$(curl -s -f -X POST "$BASE/api/network/intellisense" \
    -H "Content-Type: application/json" \
    -d '{"from_node_id":"J1","new_junction_lat":12.9082,"new_junction_lng":77.4853}')
ISENSE_DIAM=$(echo "$ISENSE_RES" | python -c "import sys, json; d=json.load(sys.stdin); print(d.get('recommended_diameter_mm',0))")
echo "PASSED (recommended_diameter=${ISENSE_DIAM}mm)"

# 9. IoT state & Pipe Condition / RUL transparency (v1.1)
echo -n "[9/11] Testing GET /api/iot/state & GET /api/pipes/condition ... "
IOT_RES=$(curl -s -f ${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"} "$BASE/api/iot/state?demo_tick=true&seed=42")
echo "$IOT_RES" | python -c "import sys, json; d=json.load(sys.stdin); assert len(d.get('latest_readings',{})) >= 5"
PIPE_RES=$(curl -s -f "$BASE/api/pipes/condition")
PIPE_COUNT=$(echo "$PIPE_RES" | python -c "import sys, json; d=json.load(sys.stdin); assert d.get('rul_available') is False; print(len(d.get('pipes',[])))")
echo "PASSED (pipes=$PIPE_COUNT, rul_honest=true)"

# 10. Auto-Valve safety gate (v1.1)
echo -n "[10/11] Testing POST /api/iot/auto-valve safety gate ... "
AV_RES=$(curl -s -f -X POST "$BASE/api/iot/auto-valve" \
    ${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"} \
    -H "Content-Type: application/json" \
    -d '{"action":"confirm_close","confirm":false}')
AV_STATUS=$(echo "$AV_RES" | python -c "import sys, json; d=json.load(sys.stdin); assert d.get('status') == 'refused'; print(d.get('status',''))")
if [ "$AV_STATUS" = "refused" ]; then
    echo "PASSED (unconfirmed close refused safely)"
else
    echo "FAILED (expected 422, got $AV_CODE)"
    exit 1
fi

# 11. AI Advisor suggestions (v1.1)
echo -n "[11/11] Testing GET /api/ai/suggestions ... "
AI_RES=$(curl -s -f "$BASE/api/ai/suggestions")
AI_COUNT=$(echo "$AI_RES" | python -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('suggestions',[])))")
echo "PASSED ($AI_COUNT suggestions)"

echo "=================================================="
echo "    ALL SMOKE TESTS PASSED SUCCESSFULLY!          "
echo "=================================================="
