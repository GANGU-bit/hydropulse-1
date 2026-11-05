"""
Demo Insurance: Snapshot live API responses into frontend/public/mock/
Allows frontend to replay 100% REAL physics results offline if backend fails on stage.
All responses are validated against data_utils.schemas before writing.
"""

import datetime
import json
import os
import shutil
import sys
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_utils.schemas import (
    HealthResponse,
    NetworkResponse,
    PresetsResponse,
    LeakSimulationResponse,
)

BASE_URL = os.environ.get("BASE", "http://localhost:8000").rstrip("/")
SNAPSHOT_SEED = 7  # Fixed seed per SOURCE_OF_TRUTH.md Â§6


def snapshot_fixtures():
    mock_dir = REPO_ROOT / "frontend" / "public" / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to live backend at {BASE_URL}...")
    try:
        health_check = requests.get(f"{BASE_URL}/api/health", timeout=3.0)
        if health_check.status_code != 200:
            print(f"Error: API returned status {health_check.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: Could not connect to API at {BASE_URL}. Ensure backend is running.")
        print(f"Details: {e}")
        sys.exit(1)

    # Backup existing fixtures
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = mock_dir / "backups" / f"backup_{timestamp}"
    existing_json_files = list(mock_dir.glob("*.json"))
    if existing_json_files:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in existing_json_files:
            shutil.copy2(f, backup_dir / f.name)
        print(f"Backed up {len(existing_json_files)} existing fixtures to {backup_dir.relative_to(REPO_ROOT)}")

    def get_json(path: str, timeout: float) -> dict:
        response = requests.get(f"{BASE_URL}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()

    # 1. Health
    print("Snapshotting GET /api/health ...")
    health_data = get_json("/api/health", 3.0)
    HealthResponse.model_validate(health_data)
    with open(mock_dir / "health.json", "w", encoding="utf-8") as f:
        json.dump(health_data, f, indent=2)

    # 2. Network
    print("Snapshotting GET /api/network ...")
    net_data = get_json("/api/network", 5.0)
    NetworkResponse.model_validate(net_data)
    with open(mock_dir / "network.json", "w", encoding="utf-8") as f:
        json.dump(net_data, f, indent=2)

    # 3. Presets
    print("Snapshotting GET /api/presets ...")
    presets_data = get_json("/api/presets", 3.0)
    PresetsResponse.model_validate(presets_data)
    with open(mock_dir / "presets.json", "w", encoding="utf-8") as f:
        json.dump(presets_data, f, indent=2)

    # 4. Simulate Leak for each preset
    presets_list = presets_data.get("presets", [])
    first_resp = None

    for p in presets_list:
        pid = p["id"]
        print(f"Snapshotting POST /api/simulate-leak for preset '{pid}' (seed={SNAPSHOT_SEED})...")
        payload = {
            "target_node_id": p["target_node_id"],
            "leak_area_cm2": p["leak_area_cm2"],
            "noise_pct": p["noise_pct"],
            "seed": SNAPSHOT_SEED,
        }
        r = requests.post(
            f"{BASE_URL}/api/simulate-leak",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=8.0,
        )
        r.raise_for_status()

        sim_data = r.json()
        LeakSimulationResponse.model_validate(sim_data)

        target_file = mock_dir / f"simulate_leak_{pid}.json"
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(sim_data, f, indent=2)

        if first_resp is None:
            first_resp = sim_data

    # 5. Default simulate leak
    if first_resp is None:
        raise RuntimeError("The API returned no presets; cannot create a default fixture.")

    with open(mock_dir / "simulate_leak_default.json", "w", encoding="utf-8") as f:
        json.dump(first_resp, f, indent=2)

    print("\n=== Snapshot Complete! ===")
    print(f"Updated mock files in {mock_dir.relative_to(REPO_ROOT)}:")
    for f in sorted(mock_dir.glob("*.json")):
        print(f"  - {f.name} ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    snapshot_fixtures()

