"""Honest accuracy / latency benchmark → backend/benchmark_report.md.

Run from the repo root:  python backend/benchmark.py [--sample 80] [--seed 2026]

For a seeded sample of junctions (all of them if N ≤ sample), each trial
injects a leak of random area U(10, 60) cm² and runs the full detection
pipeline. Repeated for noise 0 / 1.5 / 3 / 5 % and for two sensor strategies.
The pitch quotes this file — never edit the numbers by hand.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.hydraulic_engine import HydraulicEngine  # noqa: E402

NOISE_LEVELS = (0.0, 1.5, 3.0, 5.0)
STRATEGIES = ("betweenness_separated", "betweenness_top")


def run_trials(engine: HydraulicEngine, nodes: list[str], noise: float, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    top1 = top3 = 0
    errs, ms = [], []
    for i, j in enumerate(nodes):
        area = float(rng.uniform(10.0, 60.0))
        r = engine.simulate_and_triangulate(j, area, noise, seed=seed + i)
        ids = [c["node_id"] for c in r["predicted_top_candidates"]]
        top1 += ids[0] == j
        top3 += j in ids[:3]
        errs.append(r["localization_error_m"])
        ms.append(r["compute_time_ms"])
    n = len(nodes)
    return {
        "noise": noise, "n": n,
        "top1": 100.0 * top1 / n, "top3": 100.0 * top3 / n,
        "err_mean": float(np.mean(errs)), "err_median": float(np.median(errs)),
        "err_p90": float(np.percentile(errs, 90)),
        "ms_p50": float(np.percentile(ms, 50)), "ms_p95": float(np.percentile(ms, 95)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=80)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="backend/benchmark_report.md")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    t0 = time.perf_counter()
    base = HydraulicEngine().boot()
    boot_s = time.perf_counter() - t0
    rng = np.random.default_rng(args.seed)
    nodes = list(base.junctions)
    if len(nodes) > args.sample:
        nodes = sorted(rng.choice(nodes, size=args.sample, replace=False).tolist())

    rows: list[tuple[str, list[str], dict]] = []
    for strategy in STRATEGIES:
        eng = base if strategy == "betweenness_separated" else base.clone_with_sensors(strategy)
        for noise in NOISE_LEVELS:
            res = run_trials(eng, nodes, noise, args.seed)
            rows.append((strategy, list(eng.sensor_ids), res))
            print(f"{strategy:24s} noise {noise:>3}% top1 {res['top1']:5.1f}% top3 {res['top3']:5.1f}% "
                  f"median err {res['err_median']:6.1f} m p95 {res['ms_p95']:6.0f} ms")

    headline = next(r for s, _, r in rows if s == "betweenness_separated" and r["noise"] == 1.5)
    lines = [
        "# HydroPulse benchmark report",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `backend/benchmark.py` "
        f"(seed {args.seed}). **Do not edit by hand.**",
        "",
        f"- Network: `{base.network_name}` — {len(base.junctions)} junctions, "
        f"{len(base.wn.pipe_name_list)} pipes ({'Net3 FALLBACK' if base.is_fallback else base.inp_path})",
        f"- Leak solver path: `{base.solver_path}`; engine boot {boot_s:.1f} s",
        f"- Trials per row: {len(nodes)} junctions, leak area U(10, 60) cm², one leak per trial",
        "- Localization error = haversine distance between the injected and the top-ranked junction",
        "",
        "| Sensor strategy | Sensors | Noise % | Top-1 % | Top-3 % | Mean err (m) | Median err (m) "
        "| P90 err (m) | P50 latency (ms) | P95 latency (ms) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for strategy, sensors, r in rows:
        lines.append(
            f"| {strategy} | {', '.join(sensors)} | {r['noise']} | {r['top1']:.1f} | {r['top3']:.1f} | "
            f"{r['err_mean']:.1f} | {r['err_median']:.1f} | {r['err_p90']:.1f} | {r['ms_p50']:.0f} | {r['ms_p95']:.0f} |"
        )
    lines += [
        "",
        "## Plain-English summary",
        "",
        f"On the `{base.network_name}` network ({len(base.junctions)} junctions) with 5 sentinel pressure "
        f"sensors and 1.5% sensor noise, HydroPulse ranked the true leaking junction first in "
        f"{headline['top1']:.0f}% of {headline['n']} simulated bursts and in the top 3 in {headline['top3']:.0f}%. "
        f"The median distance between the predicted and the true junction was {headline['err_median']:.0f} m "
        f"(90th percentile {headline['err_p90']:.0f} m), and 95% of detections completed in under "
        f"{headline['ms_p95']:.0f} ms. These are simulation results on a digital twin, not field measurements.",
        "",
    ]
    if base.network_name != "bangalore_kengeri":
        lines += [
            f"> **Caveat:** `{base.network_name}` is not the real OSM Kengeri network. Re-run this benchmark after "
            "`python backend/generate_bangalore_dma.py` (OSM) has been committed, and quote only those numbers.",
            "",
        ]
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out} in {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
