# HydroPulse Mock Fixtures

Phase A mock data for frontend development and offline fallback.
All fixtures strictly mirror API Contract v1.0 and validate against `data_utils.schemas`.

## Endpoint to Fixture Mapping

| Endpoint | Fixture File |
| :--- | :--- |
| `GET /api/network` | `network.json` |
| `GET /api/presets` | `presets.json` |
| `GET /api/health` | `health.json` |
| `POST /api/simulate-leak` | `simulate_leak_<preset_id>.json` |
| `POST /api/simulate-leak` (fallback) | `simulate_leak_default.json` |

## Presets Included
- `kengeri_mainline_rupture` (target J35, 45 cmÂ², 1.5% noise)
- `residential_lane_seep` (target J80, 8 cmÂ², 3% noise)
- `joint_crack_junction` (target J44, 20 cmÂ², 2% noise)
