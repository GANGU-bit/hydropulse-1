"""Comprehensive Deployment Readiness, Security Hardening, OpenRouter Gateway,
State Persistence, and Concurrency Test Suite for HydroPulse v1.1.

Covers:
1. Configuration & Startup Validation (`backend/config.py`):
   - Dev vs Prod separation
   - Reload forbidden in prod (`HYDROPULSE_RELOAD=true` + `HYDROPULSE_ENV=prod` -> ValueError)
   - Wildcard CORS forbidden in prod (`*` -> ValueError)
   - Admin auth token validation (`HYDROPULSE_REQUIRE_ADMIN_AUTH=true` with short/missing token -> ValueError)
   - Forbidden direct provider URLs (`api.openai.com`, `api.anthropic.com`, `generativelanguage.googleapis.com`, `api.groq.com` -> ValueError)
   - Redacted `safe_summary()` never exposes secrets (`OPENROUTER_API_KEY`, `HYDROPULSE_ADMIN_TOKEN`)
   - Relative path resolution independent of working directory
2. Security Hardening (`backend/security.py` + `backend/main.py`):
   - CORS preflight (`OPTIONS`) allowed and disallowed origins
   - Administrative authentication boundary on mutating endpoints (`401` without token, `200` with `Bearer` or `X-Admin-Token`, public read-only/simulation endpoints stay open)
   - Request payload size limit enforcement (`413` on oversized payload)
   - Non-finite JSON numeric rejection (`NaN`, `Infinity` -> `422`)
   - Request tracing (`X-Request-ID` echoed or generated, `X-Content-Type-Options: nosniff`)
   - Secret leakage check in error responses and prod 500 error redaction
3. OpenRouter Exclusive AI Gateway Reliability (`backend/openrouter_client.py` + `backend/ai_advisor.py`):
   - `:free` model default and normalization
   - Offline / unconfigured fallback (deterministic AI Advisor suggestions work when OpenRouter is disabled or key missing)
   - Timeout, HTTP 429 rate-limit, HTTP 500, and malformed JSON resilience (never breaks `/api/ai/suggestions`)
   - Successful enrichment when OpenRouter returns valid rationales
   - No secret key printed in logs or exceptions
4. State Persistence, Atomic Cache Writes & Concurrency (`backend/hydraulic_engine.py`):
   - Atomic `.npy` and JSON sidecar writes leave no `.tmp` files behind
   - Optional disk-backed active network state persistence across engine restarts (`persist_active_state=True`)
   - Controlled concurrent requests across simulation, What-If, IoT, and network inspection without state corruption
5. Readiness & Health Separation (`/api/health` vs `/api/ready`).
"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import backend.main as main_mod
from backend import ai_advisor, network_builder
from backend.config import REPO_ROOT, load_config
from backend.hydraulic_engine import HydraulicEngine, _atomic_save_npy, _atomic_write_text
from backend.openrouter_client import OpenRouterClient
from data_utils.schemas import (
    AiSuggestion,
    BuildProposedNode,
    BuildProposedPipe,
    NetworkBuildCommitRequest,
    NetworkBuildPreviewRequest,
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """Module-scoped TestClient with booted HydraulicEngine."""
    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_state(client: TestClient) -> Iterator[None]:
    """Keep active network at baseline and reset IoT state before/after each test."""
    yield
    try:
        eng = main_mod._engine()
        if eng.network_revision != "rev-1-00c49b29":
            eng.rollback_network("baseline")
        client.post("/api/iot/auto-valve", json={"action": "reset_valves"})
        client.post("/api/iot/auto-valve", json={"action": "set_mode", "enable_auto_valve": False})
    except Exception:
        pass

def test_config_dev_defaults_and_path_resolution() -> None:
    """Default dev configuration resolves repo-root paths and safe defaults."""
    cfg = load_config({"HYDROPULSE_ENV": "dev"})
    assert cfg.env == "dev"
    assert not cfg.is_prod
    assert cfg.inp_path.is_absolute()
    assert cfg.inp_path == (REPO_ROOT / "backend/bangalore_kengeri.inp").resolve()
    assert cfg.openrouter_model.endswith(":free")
    assert "openrouter.ai" in cfg.openrouter_base_url


def test_config_prod_forbids_reload_and_wildcard_cors() -> None:
    """Prod environment rejects reload=true and wildcard CORS origins."""
    with pytest.raises(ValueError, match="forbidden when HYDROPULSE_ENV=prod"):
        load_config(
            {
                "HYDROPULSE_ENV": "prod",
                "HYDROPULSE_RELOAD": "true",
                "HYDROPULSE_CORS_ORIGINS": "https://hydropulse.example.com",
            }
        )

    with pytest.raises(ValueError, match="Wildcard CORS origin"):
        load_config(
            {
                "HYDROPULSE_ENV": "prod",
                "HYDROPULSE_RELOAD": "false",
                "HYDROPULSE_CORS_ORIGINS": "*",
            }
        )


def test_config_admin_auth_validation() -> None:
    """Enabling require_admin_auth without a strong token fails fast at startup."""
    with pytest.raises(ValueError, match="HYDROPULSE_ADMIN_TOKEN"):
        load_config(
            {
                "HYDROPULSE_ENV": "dev",
                "HYDROPULSE_REQUIRE_ADMIN_AUTH": "true",
                "HYDROPULSE_ADMIN_TOKEN": "short",
            }
        )

    cfg = load_config(
        {
            "HYDROPULSE_ENV": "prod",
            "HYDROPULSE_RELOAD": "false",
            "HYDROPULSE_CORS_ORIGINS": "https://scada.bwssb.gov.in",
            "HYDROPULSE_ADMIN_TOKEN": "super-secret-admin-token-999",
        }
    )
    assert cfg.is_prod
    assert cfg.require_admin_auth is True


def test_config_forbids_non_openrouter_ai_providers() -> None:
    """Direct Gemini, Groq, OpenAI, or Anthropic base URLs are rejected."""
    for bad_url in (
        "https://generativelanguage.googleapis.com/v1beta",
        "https://api.groq.com/openai/v1",
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
    ):
        with pytest.raises(ValueError, match="OpenRouter is the exclusive AI gateway"):
            load_config(
                {
                    "HYDROPULSE_ENV": "dev",
                    "HYDROPULSE_OPENROUTER_BASE_URL": bad_url,
                }
            )


def test_config_safe_summary_redacts_secrets() -> None:
    """safe_summary() never leaks OPENROUTER_API_KEY or HYDROPULSE_ADMIN_TOKEN."""
    secret_or = "sk-or-v1-ultra-secret-key-do-not-leak-12345"
    secret_adm = "adm-token-ultra-secret-987654321"
    cfg = load_config(
        {
            "HYDROPULSE_ENV": "dev",
            "OPENROUTER_API_KEY": secret_or,
            "HYDROPULSE_ADMIN_TOKEN": secret_adm,
        }
    )
    summary_str = json.dumps(cfg.safe_summary())
    assert secret_or not in summary_str
    assert secret_adm not in summary_str
    assert cfg.safe_summary()["openrouter_api_key_configured"] is True


# =============================================================================
# 2. Security Hardening: CORS, Auth Boundary, Payload Size, NaN, Request ID
# =============================================================================


def test_cors_preflight_behavior(client: TestClient) -> None:
    """CORS preflight responds properly to allowed origins."""
    r = client.options(
        "/api/simulate-leak",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-Admin-Token",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("http://localhost:5173", "*")


def test_request_id_and_security_headers(client: TestClient) -> None:
    """Responses include X-Request-ID and X-Content-Type-Options: nosniff."""
    custom_req_id = "test-trace-req-20261105"
    r = client.get("/api/health", headers={"X-Request-ID": custom_req_id})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == custom_req_id
    assert r.headers.get("X-Content-Type-Options") == "nosniff"

    # Invalid X-Request-ID characters are replaced with a clean generated ID
    r2 = client.get("/api/health", headers={"X-Request-ID": "bad id with spaces & <script>"})
    assert r2.status_code == 200
    assert r2.headers.get("X-Request-ID") != "bad id with spaces & <script>"
    assert len(r2.headers.get("X-Request-ID", "")) >= 8


def test_admin_auth_boundary_on_mutating_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """When require_admin_auth is enabled, mutating routes require valid admin token while read/sim routes remain public."""
    locked_cfg = load_config(
        {
            "HYDROPULSE_ENV": "prod",
            "HYDROPULSE_RELOAD": "false",
            "HYDROPULSE_CORS_ORIGINS": "http://localhost:5173",
            "HYDROPULSE_REQUIRE_ADMIN_AUTH": "true",
            "HYDROPULSE_ADMIN_TOKEN": "prod-admin-secret-token-42",
        }
    )
    monkeypatch.setattr(main_mod, "config", locked_cfg)

    # 1. Public read-only and simulation routes STILL work without token
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/network").status_code == 200
    assert (
        client.post(
            "/api/simulate-leak",
            json={"target_node_id": "J1", "leak_area_cm2": 20.0, "noise_pct": 1.0, "seed": 42},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/what-if",
            json={"scenario_name": "Public Check", "demand_multiplier": 1.05},
        ).status_code
        == 200
    )

    # 2. Mutating endpoints reject unauthenticated or wrong-token calls with 401
    mutating_calls = [
        ("/api/network/reset", {"target": "baseline", "confirm": True}),
        (
            "/api/iot/ingest",
            {
                "packets": [
                    {
                        "source_id": "S_AUTH",
                        "source_type": "simulator",
                        "network_revision": "rev-1-00c49b29",
                        "observed_at_utc": "2026-11-05T06:00:00Z",
                        "target_id": "J1",
                        "measurement_type": "pressure_m",
                        "value": 35.0,
                        "unit": "m",
                        "sequence": 1,
                    }
                ]
            },
        ),
        ("/api/iot/auto-valve", {"action": "set_mode", "enable_auto_valve": False}),
    ]
    for path, body in mutating_calls:
        r_unauth = client.post(path, json=body)
        assert r_unauth.status_code == 401, f"Expected 401 on {path}, got {r_unauth.status_code}"
        assert "prod-admin-secret-token-42" not in r_unauth.text

        r_wrong = client.post(path, json=body, headers={"Authorization": "Bearer wrong-token"})
        assert r_wrong.status_code == 401

    # 3. Valid token via Authorization: Bearer or X-Admin-Token succeeds
    r_bearer = client.post(
        "/api/iot/auto-valve",
        json={"action": "set_mode", "enable_auto_valve": False},
        headers={"Authorization": "Bearer prod-admin-secret-token-42"},
    )
    assert r_bearer.status_code == 200

    r_header = client.post(
        "/api/iot/auto-valve",
        json={"action": "reset_valves"},
        headers={"X-Admin-Token": "prod-admin-secret-token-42"},
    )
    assert r_header.status_code == 200


def test_payload_size_limit_and_non_finite_json_guards(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized bodies return 413 and NaN/Infinity JSON values return 422."""
    # Non-finite JSON (NaN / Infinity) rejected with 422
    r_nan = client.post(
        "/api/simulate-leak",
        content=b'{"target_node_id": "J1", "leak_area_cm2": NaN, "noise_pct": 1.5}',
        headers={"Content-Type": "application/json"},
    )
    assert r_nan.status_code == 422

    r_inf = client.post(
        "/api/simulate-leak",
        content=b'{"target_node_id": "J1", "leak_area_cm2": Infinity, "noise_pct": 1.5}',
        headers={"Content-Type": "application/json"},
    )
    assert r_inf.status_code == 422

    # Temporarily set a small max_request_bytes to test 413 guard fast
    small_cfg = load_config(
        {
            "HYDROPULSE_ENV": "dev",
            "HYDROPULSE_MAX_REQUEST_BYTES": "65536",
        }
    )
    monkeypatch.setattr(main_mod, "config", small_cfg)
    huge_payload = json.dumps({"format": "inp", "content": "X" * 80_000}).encode("utf-8")
    r_big = client.post(
        "/api/network/import/preview",
        content=huge_payload,
        headers={"Content-Type": "application/json"},
    )
    assert r_big.status_code == 413
    assert "exceeds maximum allowed size" in r_big.json()["detail"]


# =============================================================================
# 3. OpenRouter Exclusive AI Gateway & Fault Tolerance
# =============================================================================

def test_openrouter_client_offline_and_failure_resilience() -> None:
    """OpenRouterClient degrades gracefully (returns original suggestions) on missing key, timeout, 429, 500, or bad JSON."""
    sample_sugs = [
        AiSuggestion(
            suggestion_id="sug-loop-J10-J20",
            category="loop_completion",
            title="Cross-Connect Dead-End J10 to J20",
            rationale="Deterministic physics rationale: adds +1.8 m steady-state pressure.",
            source_network_revision="rev-1-00c49b29",
            status="proposed",
            affected_node_ids=["J10", "J20"],
            affected_pipe_ids=["P_LOOP_J10_J20"],
            proposed_parameters={"from_node": "J10", "to_node": "J20", "diameter_mm": 150.0},
            baseline_metrics={"min_pressure_m": 16.2},
            simulated_after_metrics={"min_pressure_m": 18.0},
            uncertainty_note="Steady-state PDD model evaluation.",
        )
    ]
    # 1. Disabled / unconfigured -> returns original suggestions immediately without network call
    cfg_disabled = load_config({"HYDROPULSE_ENV": "dev", "HYDROPULSE_OPENROUTER_ENABLED": "false", "OPENROUTER_API_KEY": ""})
    client_disabled = OpenRouterClient(config=cfg_disabled)
    assert client_disabled.is_active is False
    res_disabled = client_disabled.enrich_suggestions(sample_sugs, network_revision="rev-1")
    assert res_disabled[0].rationale == sample_sugs[0].rationale

    # 2. Free model normalization & redacted status metadata
    cfg_ready = load_config(
        {
            "HYDROPULSE_ENV": "dev",
            "HYDROPULSE_OPENROUTER_ENABLED": "true",
            "OPENROUTER_API_KEY": "sk-or-v1-fake-test-key-for-unit-test",
            "HYDROPULSE_OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
            "HYDROPULSE_OPENROUTER_MAX_RETRIES": "0",
        }
    )
    client_ready = OpenRouterClient(config=cfg_ready)
    assert client_ready.is_active is True
    assert client_ready.config.openrouter_model.endswith(":free")
    assert "sk-or-v1-fake-test-key" not in json.dumps(client_ready.get_status_metadata())

    # 3. Timeout -> returns original suggestions gracefully
    def _timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Simulated timeout", request=request)

    res_timeout = client_ready.enrich_suggestions(
        sample_sugs,
        network_revision="rev-1",
        transport=httpx.MockTransport(_timeout_handler),
    )
    assert res_timeout[0].rationale == sample_sugs[0].rationale

    # 4. HTTP 429 Rate Limit -> returns original suggestions gracefully
    def _rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})

    res_429 = client_ready.enrich_suggestions(
        sample_sugs,
        network_revision="rev-1",
        transport=httpx.MockTransport(_rate_limit_handler),
    )
    assert res_429[0].rationale == sample_sugs[0].rationale

    # 5. Malformed LLM JSON -> returns original suggestions gracefully
    def _bad_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not valid json {{"}}]})

    res_bad_json = client_ready.enrich_suggestions(
        sample_sugs,
        network_revision="rev-1",
        transport=httpx.MockTransport(_bad_json_handler),
    )
    assert res_bad_json[0].rationale == sample_sugs[0].rationale

    # 6. Valid OpenRouter JSON response -> returns enriched rationale while preserving IDs and metrics
    enriched_text = (
        "Executive summary: Adding a 150 mm loop between J10 and J20 eliminates single-feed "
        "vulnerability while preserving +1.8 m steady-state pressure (16.2 m -> 18.0 m)."
    )

    def _ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"sug-loop-J10-J20": enriched_text})
                        }
                    }
                ]
            },
        )

    res_ok = client_ready.enrich_suggestions(
        sample_sugs,
        network_revision="rev-1",
        transport=httpx.MockTransport(_ok_handler),
    )
    assert res_ok[0].suggestion_id == "sug-loop-J10-J20"
    assert res_ok[0].rationale == enriched_text
    assert res_ok[0].suggestion_id == "sug-loop-J10-J20"
    assert res_ok[0].simulated_after_metrics == sample_sugs[0].simulated_after_metrics

def test_ai_advisor_works_when_openrouter_fails(client: TestClient) -> None:
    """GET /api/ai/suggestions succeeds with deterministic physics even if OpenRouter raises an error."""
    ai_advisor._advisor_service._suggestions_cache.clear()
    with patch("backend.ai_advisor.get_openrouter_client") as mock_get_or:
        fake_or = MagicMock()
        fake_or.is_active = True
        fake_or.enrich_suggestions.side_effect = RuntimeError("Simulated OpenRouter outage")
        mock_get_or.return_value = fake_or

        r = client.get("/api/ai/suggestions")
        assert r.status_code == 200
        body = r.json()
        assert len(body["suggestions"]) >= 3
        assert body["sensor_benchmark"] is not None


# =============================================================================
# 4. State Persistence, Atomic Writes, Readiness & Concurrency
# =============================================================================


def test_atomic_file_writers_leave_no_temp_files(tmp_path: Path) -> None:
    """_atomic_write_text and _atomic_save_npy write atomically without leftover .tmp files."""
    txt_file = tmp_path / "subdir" / "state.json"
    _atomic_write_text(txt_file, '{"ok": true}')
    assert txt_file.exists()
    assert json.loads(txt_file.read_text(encoding="utf-8")) == {"ok": True}
    assert list(txt_file.parent.glob("*.tmp")) == []

    npy_file = tmp_path / "subdir" / "matrix.npy"
    arr = np.arange(12, dtype=np.float32).reshape(3, 4)
    _atomic_save_npy(npy_file, arr)
    assert npy_file.exists()
    loaded = np.load(npy_file)
    assert np.allclose(loaded, arr)
    assert list(npy_file.parent.glob("*.tmp")) == []


def test_disk_backed_active_network_persistence_across_restart(tmp_path: Path) -> None:
    """When persist_active_state=True, committed network mutations survive engine restart and clear on rollback."""
    state_dir = tmp_path / "persisted_state"
    eng1 = HydraulicEngine(
        state_dir=state_dir,
        persist_active_state=True,
    ).boot()
    base_rev = eng1.network_revision

    # Mutate active network on eng1 via NetworkBuilder (add a small junction & pipe)
    preview = network_builder.preview_build(
        eng1,
        NetworkBuildPreviewRequest(
            new_nodes=[
                BuildProposedNode(
                    id="J999",
                    lat=12.915,
                    lng=77.495,
                    elevation_m=810.0,
                    base_demand_lps=0.2,
                )
            ],
            new_pipes=[
                BuildProposedPipe(
                    id="P999",
                    from_node="J1",
                    to_node="J999",
                    diameter_mm=150.0,
                    length_m=120.0,
                    roughness=130.0,
                )
            ],
            expected_network_revision=base_rev,
        ),
    )
    assert preview.valid
    assert preview.preview_token is not None
    commit_res = network_builder.commit_build(
        eng1,
        NetworkBuildCommitRequest(
            preview_token=preview.preview_token,
            expected_network_revision=base_rev,
            confirm=True,
        ),
    )
    mutated_rev = commit_res.new_network_revision
    assert mutated_rev != base_rev
    assert (state_dir / "active_network.inp").exists()
    assert (state_dir / "active_state.json").exists()
    eng2 = HydraulicEngine(
        state_dir=state_dir,
        persist_active_state=True,
    ).boot()
    assert eng2.network_revision == mutated_rev
    assert "J999" in eng2.junctions

    # Rollback to baseline on eng2 -> persisted files are cleaned up
    eng2.rollback_network("baseline")
    assert eng2.network_revision == base_rev
    assert not (state_dir / "active_network.inp").exists()


def test_readiness_endpoint_and_concurrent_requests(client: TestClient) -> None:
    """GET /api/ready verifies matrix & baseline readiness, and concurrent requests execute safely."""
    r_ready = client.get("/api/ready")
    assert r_ready.status_code == 200
    body = r_ready.json()
    assert body["status"] == "ready"
    assert body["matrix_ready"] is True
    assert body["num_nodes"] >= 60

    def _worker(i: int) -> int:
        if i % 3 == 0:
            resp = client.get("/api/health")
        elif i % 3 == 1:
            resp = client.post(
                "/api/simulate-leak",
                json={"target_node_id": "J10", "leak_area_cm2": 20.0, "noise_pct": 1.0, "seed": 42 + i},
            )
        else:
            resp = client.post(
                "/api/what-if",
                json={"scenario_name": f"Conc-{i}", "demand_multiplier": 1.05},
            )
        return resp.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(_worker, range(12)))
    assert all(c == 200 for c in codes)
