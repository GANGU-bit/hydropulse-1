# HydroPulse benchmark report

Generated 2026-11-05T06:34:29+00:00 by `backend/benchmark.py` (seed 2026). **Do not edit by hand.**

- Network: `kengeri_synthetic` — 129 junctions, 203 pipes (backend/bangalore_kengeri.inp)
- Leak solver path: `wntr_reusable_model`; engine boot 0.4 s
- Trials per row: 80 junctions, leak area U(10, 60) cm², one leak per trial
- Localization error = haversine distance between the injected and the top-ranked junction

| Sensor strategy | Sensors | Noise % | Top-1 % | Top-3 % | Mean err (m) | Median err (m) | P90 err (m) | P50 latency (ms) | P95 latency (ms) |
|---|---|---|---|---|---|---|---|---|---|
| betweenness_separated | J72, J29, J64, J95, J47 | 0.0 | 86.2 | 100.0 | 13.6 | 0.0 | 70.3 | 297 | 517 |
| betweenness_separated | J72, J29, J64, J95, J47 | 1.5 | 51.2 | 85.0 | 79.7 | 0.0 | 226.5 | 297 | 493 |
| betweenness_separated | J72, J29, J64, J95, J47 | 3.0 | 37.5 | 67.5 | 123.7 | 105.6 | 293.0 | 291 | 517 |
| betweenness_separated | J72, J29, J64, J95, J47 | 5.0 | 27.5 | 48.8 | 156.3 | 127.2 | 378.0 | 279 | 488 |
| betweenness_top | J72, J71, J83, J62, J73 | 0.0 | 66.2 | 87.5 | 50.6 | 0.0 | 146.4 | 134 | 348 |
| betweenness_top | J72, J71, J83, J62, J73 | 1.5 | 20.0 | 42.5 | 223.2 | 187.2 | 475.5 | 136 | 333 |
| betweenness_top | J72, J71, J83, J62, J73 | 3.0 | 16.2 | 30.0 | 295.5 | 248.5 | 580.8 | 129 | 332 |
| betweenness_top | J72, J71, J83, J62, J73 | 5.0 | 15.0 | 25.0 | 391.2 | 320.2 | 783.3 | 127 | 359 |

## Plain-English summary

On the `kengeri_synthetic` network (129 junctions) with 5 sentinel pressure sensors and 1.5% sensor noise, HydroPulse ranked the true leaking junction first in 51% of 80 simulated bursts and in the top 3 in 85%. The median distance between the predicted and the true junction was 0 m (90th percentile 226 m), and 95% of detections completed in under 493 ms. These are simulation results on a digital twin, not field measurements.

> **Caveat:** `kengeri_synthetic` is not the real OSM Kengeri network. Re-run this benchmark after `python backend/generate_bangalore_dma.py` (OSM) has been committed, and quote only those numbers.
