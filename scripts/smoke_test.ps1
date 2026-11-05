# HydroPulse Smoke Test (PowerShell)
$ErrorActionPreference = "Stop"

$BASE = if ($env:BASE) { $env:BASE } else { "http://localhost:8000" }
Write-Host "=================================================="
Write-Host "    HydroPulse API Smoke Test (Target: $BASE)    "
Write-Host "=================================================="

# 1. Health
Write-Host -NoNewline "[1/6] Testing GET /api/health ... "
$health = Invoke-RestMethod -Uri "$BASE/api/health" -Method Get
if ($health.status -ne "ok") {
    Write-Host "FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "PASSED" -ForegroundColor Green

# 2. Network
Write-Host -NoNewline "[2/6] Testing GET /api/network ... "
$net = Invoke-RestMethod -Uri "$BASE/api/network" -Method Get
$nodeCount = $net.nodes.Count
$pipeCount = $net.pipes.Count
$sensorCount = $net.sensor_ids.Count
Write-Host "PASSED ($($net.network_name): $nodeCount nodes, $pipeCount pipes, $sensorCount sensors)" -ForegroundColor Green

# 3. Presets
Write-Host -NoNewline "[3/6] Testing GET /api/presets ... "
$presets = Invoke-RestMethod -Uri "$BASE/api/presets" -Method Get
$presetCount = $presets.presets.Count
if ($presetCount -lt 1) {
    Write-Host "FAILED (no presets found)" -ForegroundColor Red
    exit 1
}
Write-Host "PASSED ($presetCount presets)" -ForegroundColor Green

# 4. Simulate presets
Write-Host "[4/6] Testing POST /api/simulate-leak for presets:"
foreach ($p in $presets.presets) {
    $body = @{
        target_node_id = $p.target_node_id
        leak_area_cm2 = $p.leak_area_cm2
        noise_pct = $p.noise_pct
        seed = 42
    } | ConvertTo-Json
    $sim = Invoke-RestMethod -Uri "$BASE/api/simulate-leak" -Method Post -Body $body -ContentType "application/json"
    $top = $sim.predicted_top_candidates[0]
    Write-Host "   -> Preset [$($p.id)]: target=$($p.target_node_id) | predicted=$($top.node_id) ($($top.confidence_pct)%) | err=$($sim.localization_error_m)m | tankers=$($top.tankers_lost_per_day)/d | $($sim.compute_time_ms)ms" -ForegroundColor Cyan
}

# 5. Bad node id -> 404
Write-Host -NoNewline "[5/6] Testing POST /api/simulate-leak with invalid node (expect 404) ... "
$body404 = @{ target_node_id = "NONEXISTENT_NODE_999"; leak_area_cm2 = 25.0; noise_pct = 1.5 } | ConvertTo-Json
try {
    $res = Invoke-WebRequest -Uri "$BASE/api/simulate-leak" -Method Post -Body $body404 -ContentType "application/json" -ErrorAction Stop
    Write-Host "FAILED (expected 404, got 200)" -ForegroundColor Red
    exit 1
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 404) {
        Write-Host "PASSED (received 404)" -ForegroundColor Green
    } else {
        Write-Host "FAILED (expected 404, got $statusCode)" -ForegroundColor Red
        exit 1
    }
}

# 6. Invalid area -> 422
Write-Host -NoNewline "[6/6] Testing POST /api/simulate-leak with leak_area_cm2=500 (expect 422) ... "
$body422 = @{ target_node_id = "J1"; leak_area_cm2 = 500.0; noise_pct = 1.5 } | ConvertTo-Json
try {
    $res = Invoke-WebRequest -Uri "$BASE/api/simulate-leak" -Method Post -Body $body422 -ContentType "application/json" -ErrorAction Stop
    Write-Host "FAILED (expected 422, got 200)" -ForegroundColor Red
    exit 1
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 422) {
        Write-Host "PASSED (received 422)" -ForegroundColor Green
    } else {
        Write-Host "FAILED (expected 422, got $statusCode)" -ForegroundColor Red
        exit 1
    }
}

Write-Host "=================================================="
Write-Host "    ALL SMOKE TESTS PASSED SUCCESSFULLY!          " -ForegroundColor Green
Write-Host "=================================================="

