Exit code: 0
Wall time: 4.4 seconds
Output:
#!/usr/bin/env bash
set -e

BASE="${BASE:-http://localhost:8000}"
echo "=================================================="
echo "    HydroPulse API Smoke Test (Target: $BASE)    "
echo "=================================================="

# Check if curl is available
if ! command -v curl &>/dev/null; then
    echo "Error: curl is required for smoke tests."
    exit 1
fi

# 1. Health check
echo -n "[1/6] Testing GET /api/health ... "
HEALTH_RES=$(curl -s -f "$BASE/api/health")
echo "$HEALTH_RES" | python -m json.tool > /dev/null
STATUS=$(echo "$HEALTH_RES" | python -c "import sys, json; print(json.load(sys.stdin).get('status', ''))")
if [ "$STATUS" != "ok" ]; then
    echo "FAILED (status is not ok)"
    exit 1
fi
echo "PASSED"

# 2. Network check
echo -n "[2/6] Testing GET /api/network ... "
NET_RES=$(curl -s -f "$BASE/api/network")
echo "$NET_RES" | python -m json.tool > /dev/null
NET_STATS=$(echo "$NET_RES" | python -c "import sys, json; d=json.load(sys.stdin); print(f'name={d.get(\"network_name\")} nodes={len(d.get(\"nodes\",[]))} pipes={len(d.get(\"pipes\",[]))} sensors={len(d.get(\"sensor_ids\",[]))}')")
echo "PASSED ($NET_STATS)"

# 3. Presets check
echo -n "[3/6] Testing GET /api/presets ... "
PRESETS_RES=$(curl -s -f "$BASE/api/presets")
echo "$PRESETS_RES" | python -m json.tool > /dev/null
PRESET_COUNT=$(echo "$PRESETS_RES" | python -c "import sys, json; print(len(json.load(sys.stdin).get('presets', [])))")
if [ "$PRESET_COUNT" -lt 1 ]; then
    echo "FAILED (no presets found)"
    exit 1
fi
echo "PASSED ($PRESET_COUNT presets)"

# 4. Simulate leak for each preset
echo "[4/6] Testing POST /api/simulate-leak for presets:"
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
echo -n "[5/6] Testing POST /api/simulate-leak with invalid node (expect 404) ... "
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
echo -n "[6/6] Testing POST /api/simulate-leak with leak_area_cm2=500 (expect 422) ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/simulate-leak" \
    -H "Content-Type: application/json" \
    -d '{"target_node_id":"J1","leak_area_cm2":500.0,"noise_pct":1.5}')
if [ "$HTTP_CODE" -eq 422 ]; then
    echo "PASSED (received 422)"
else
    echo "FAILED (expected 422, got $HTTP_CODE)"
    exit 1
fi

echo "=================================================="
echo "    ALL SMOKE TESTS PASSED SUCCESSFULLY!          "
echo "=================================================="

