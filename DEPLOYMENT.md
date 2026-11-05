# HydroPulse Backend — AWS EC2 Host Deployment Guide

This document covers production deployment on AWS EC2 (Ubuntu 24.04 LTS x86_64, Python 3.11) using systemd service management, native virtual environment, and Git-based rollback. The backend runs as a single-worker Uvicorn process bound to loopback only, with secrets stored in `/etc/hydropulse/hydropulse.env` (mode 0600) and persistence in `/var/lib/hydropulse`.

---

## 1. Prerequisites

### EC2 Instance Requirements

- **OS**: Ubuntu 24.04 LTS x86_64
- **Python**: 3.11 (system package or pyenv)
- **Network**: Public IP with security group allowing inbound SSH (port 22) and optional temporary tunneling
- **Storage**: At least 10 GB GP3 for repository, venv, sensitivity matrix caches, and persisted state
- **Public IP Cost Caution**: Assigning a public IP to an EC2 instance incurs hourly data transfer charges; consider using a VPC endpoint or NAT gateway for cost optimization in production.

### System Packages

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git curl
```

### Service User

Create a dedicated non-root service user for running the HydroPulse backend:

```bash
sudo useradd --system --shell /usr/sbin/nologin --home /var/lib/hydropulse hydropulse
sudo mkdir -p /var/lib/hydropulse
sudo chown hydropulse:hydropulse /var/lib/hydropulse
sudo chmod 750 /var/lib/hydropulse
```

---

## 2. Repository Checkout and Virtual Environment

Clone the repository to `/opt/hydropulse` (replace with your Git source):

```bash
sudo mkdir -p /opt/hydropulse
sudo chown hydropulse:hydropulse /opt/hydropulse
sudo -u hydropulse git clone <repository-url> /opt/hydropulse
cd /opt/hydropulse

# Run the deployment script (creates .venv, sets up service, validates configuration)
sudo bash scripts/deploy-hydropulse-host.sh
```

---

## 3. Secret Configuration

### Secret File Location

All secrets must be stored in `/etc/hydropulse/hydropulse.env` with restrictive permissions:

```bash
sudo mkdir -p /etc/hydropulse
sudo touch /etc/hydropulse/hydropulse.env
sudo chmod 600 /etc/hydropulse/hydropulse.env
sudo chown root:root /etc/hydropulse/hydropulse.env
```

### Required Environment Variables

Edit `/etc/hydropulse/hydropulse.env` with production values:

```bash
# Runtime environment
HYDROPULSE_ENV=prod

# Uvicorn bind address (loopback only - no public binding)
HYDROPULSE_HOST=127.0.0.1
HYDROPULSE_PORT=8000
HYDROPULSE_LOG_LEVEL=INFO

# Auto-reload MUST be false in production
HYDROPULSE_RELOAD=false

# CORS origins: REPLACE with exact deployed frontend origin before starting
# Wildcard (*) is forbidden in prod. Fail-closed if misconfigured.
HYDROPULSE_CORS_ORIGINS=https://frontend.example.invalid

# Admin authentication for mutating endpoints
HYDROPULSE_REQUIRE_ADMIN_AUTH=true
HYDROPULSE_ADMIN_TOKEN=<generate-strong-random-token-at-least-8-chars>

# Persistence directories (durable storage in /var/lib/hydropulse)
HYDROPULSE_STATE_DIR=/var/lib/hydropulse
HYDROPULSE_CACHE_DIR=/var/lib/hydropulse
HYDROPULSE_PERSIST_ACTIVE_STATE=true

# Network data paths (relative to repo root)
HYDROPULSE_INP=backend/bangalore_kengeri.inp
HYDROPULSE_META=backend/network_meta.json
HYDROPULSE_PRESETS=data_utils/presets.json

# Optional OpenRouter API key (leave unset to disable AI enrichment)
OPENROUTER_API_KEY=
```

**Critical**: Replace `https://frontend.example.invalid` with the exact deployed frontend origin (e.g., `https://app.yourdomain.com`) before starting the service. Incorrect CORS configuration will block all frontend requests.

### Non-Secret Template

A root-owned, non-secret template is available at `deploy/hydropulse.env.example` for reference. Do not store secrets in this file.

---

## 4. Systemd Service Installation

Install the systemd service unit from `deploy/systemd/hydropulse.service`:

The deployment script automatically installs the systemd service. If installing manually without the script:

```bash
sudo cp /opt/hydropulse/deploy/systemd/hydropulse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hydropulse.service
```

The service unit configures:
- **User**: `hydropulse` (non-root system user)
- **WorkingDirectory**: `/opt/hydropulse`
- **EnvironmentFile**: `/etc/hydropulse/hydropulse.env`
- **ExecStart**: python -m uvicorn with `--workers 1 --host 127.0.0.1 --port 8000 --no-access-log`
- **Restart**: `on-failure` with 5-second delay
- **RestartSec**: 5 seconds
- **StandardOutput/StandardError**: journal

---

## 5. Deployment Script

Use the provided deployment script `scripts/deploy-hydropulse-host.sh` to automate checkout, venv setup, and service installation:

```bash
cd /opt/hydropulse
sudo bash scripts/deploy-hydropulse-host.sh
```

The script performs:
1. Validates Ubuntu 24.04, Python 3.11, and systemd availability
2. Creates the `hydropulse` service user and `/var/lib/hydropulse` persistent directory
3. Creates `/etc/hydropulse/hydropulse.env` from template on first run (exits for operator to add secrets)
4. Validates base configuration including rejection of CORS placeholder values
5. Creates or updates the virtual environment (`.venv` in repository root)
6. Installs dependencies from `requirements.txt`
7. Installs and enables the systemd service unit
8. Restarts the service

All operations are idempotent; the script can be rerun safely after updating the repository or configuration.


---

## 6. Service Management

### Start the Service

```bash
sudo systemctl start hydropulse.service
```

### Check Service Status

```bash
sudo systemctl status hydropulse.service
```

### View Service Logs

```bash
sudo journalctl -u hydropulse.service -f
```

### Stop the Service

```bash
sudo systemctl stop hydropulse.service
```

### Restart the Service

```bash
sudo systemctl restart hydropulse.service
```

---

## 7. Health and Readiness Verification

### Liveness Check

The service listens on loopback only (127.0.0.1:8000). Verify from the host:

```bash
curl -sSf http://127.0.0.1:8000/api/health | python3 -m json.tool
```

Expected response includes:
- `status: "ok"`
- `network_name`
- `num_junctions`
- `num_pipes`
- `sensor_ids`
- `matrix_ready`
- `network_revision`
- `source`
- `can_rollback`

### Readiness Check

```bash
curl -sSf http://127.0.0.1:8000/api/ready | python3 -m json.tool
```

Returns HTTP `200` with `status: "ready"` when the hydraulic baseline and sensitivity matrix are fully computed. Returns HTTP `503` during initialization.

---

## 8. Temporary Tunneling (Cloudflare Quick Tunnel)

For temporary access without a stable hostname or production control plane, use Cloudflare Quick Tunnel targeting the loopback address:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

**Limitations**:
- Provides a temporary, ephemeral public URL
- No stable hostname
- No production-grade control plane or access controls
- Suitable only for short-lived testing or demos
- Must be terminated after use

For production deployment, use a reverse proxy (nginx, Apache) or an Application Load Balancer with proper TLS termination and origin controls.

---

## 9. Rollback and Recovery

### Git-Based Rollback

To rollback to a previous Git revision:

```bash
cd /opt/hydropulse/repo
sudo -u hydropulse git log --oneline -10
sudo -u hydropulse git checkout <revision-hash>
sudo systemctl restart hydropulse.service
```

The persisted state in `/var/lib/hydropulse` survives across restarts. If you need to reset the active network to baseline:

```bash
curl -X POST http://127.0.0.1:8000/api/network/reset \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <HYDROPULSE_ADMIN_TOKEN>" \
  -d '{"target": "baseline", "confirm": true}'
```

### Recovery from Corrupted State

If the persisted state is corrupted:

```bash
sudo systemctl stop hydropulse.service
sudo rm -rf /var/lib/hydropulse/*
sudo systemctl start hydropulse.service
```

The service will boot with the baseline Kengeri DMA network.

### Restore from Backup

If you have a backup of `/var/lib/hydropulse`:

```bash
sudo systemctl stop hydropulse.service
sudo tar xzf /path/to/hydropulse-backup.tar.gz -C /
sudo systemctl start hydropulse.service
```

---

## 10. Diagnostics

### Check Service Logs for Errors

```bash
sudo journalctl -u hydropulse.service --since "1 hour ago" | grep -i error
```

### Validate Configuration

```bash
sudo -u hydropulse /opt/hydropulse/.venv/bin/python -c \
  "from backend.config import load_config; print(load_config().safe_summary())"
```

### Verify File Permissions

```bash
ls -la /etc/hydropulse/hydropulse.env  # Should be mode 600, root:root
ls -la /var/lib/hydropulse             # Should be mode 750, hydropulse:hydropulse
```

### Check Sensitivity Matrix Cache

```bash
ls -la /var/lib/hydropulse/
```

---

## 11. Security Boundaries

### Network Exposure

- **Uvicorn binds to 127.0.0.1:8000 only** — no public interface binding
- Frontend access requires a reverse proxy (nginx, Apache) or ALB terminating TLS and forwarding to loopback
- Quick Tunnel is temporary and must not be used for production

### Secret Management

- All secrets in `/etc/hydropulse/hydropulse.env` (mode 0600, root:root)
- Never store secrets in the repository or in `.env` files
- `HYDROPULSE_ADMIN_TOKEN` and `OPENROUTER_API_KEY` are never exposed to the browser or logs

### Service Isolation

- Service runs as non-root user `hydropulse`
- Home directory is `/var/lib/hydropulse` (no shell access)
- Systemd provides process isolation and resource limits

### CORS Enforcement

- `HYDROPULSE_CORS_ORIGINS` must be exact; wildcard `*` is rejected in `prod`
- Fail-closed: incorrect CORS origin blocks all frontend requests
- Replace placeholder `https://frontend.example.invalid` before starting

### Admin Authentication

- Mutating endpoints require `HYDROPULSE_REQUIRE_ADMIN_AUTH=true`
- Admin token sent via `Authorization: Bearer <token>` or `X-Admin-Token: <token>`
- Constant-time comparison prevents timing attacks

---

## 12. Persistence and Cache Locations

### Directories

- **Active State**: `/var/lib/hydropulse/` — `active_network.inp`, `active_state.json`
- **Sensitivity Matrix Cache**: `/var/lib/hydropulse/` — `sensitivity_matrix.npy`, `.x.npy` sidecars
- **Repository**: `/opt/hydropulse/` — source code, network data files

### Atomic Writes

State files are written atomically via temporary file + `os.replace` to prevent corruption during crashes.

### Backup Strategy

Schedule regular backups of `/var/lib/hydropulse` and `/etc/hydropulse/hydropulse.env`:

```bash
sudo tar czf /backup/hydropulse-$(date +%Y%m%d).tar.gz \
  /var/lib/hydropulse /etc/hydropulse/hydropulse.env
```

---

## 13. Runtime Configuration Reference

For a complete environment variable reference, see `.env.example` in the repository root. Key production variables:

| Variable | Production Value | Description |
| :--- | :--- | :--- |
| `HYDROPULSE_ENV` | `prod` | Runtime environment |
| `HYDROPULSE_HOST` | `127.0.0.1` | Loopback-only binding |
| `HYDROPULSE_PORT` | `8000` | TCP port |
| `HYDROPULSE_RELOAD` | `false` | Auto-reload forbidden |
| `HYDROPULSE_CORS_ORIGINS` | **exact frontend origin** | No wildcard in prod |
| `HYDROPULSE_REQUIRE_ADMIN_AUTH` | `true` | Admin token required |
| `HYDROPULSE_ADMIN_TOKEN` | **secret** | >= 8 characters |
| `HYDROPULSE_PERSIST_ACTIVE_STATE` | `true` | Persist across restarts |
| `HYDROPULSE_STATE_DIR` | `/var/lib/hydropulse` | Durable state |
| `HYDROPULSE_CACHE_DIR` | `/var/lib/hydropulse` | Sensitivity matrix |
| `OPENROUTER_API_KEY` | *(optional)* | AI enrichment |

---

## 14. Troubleshooting

### Service Fails to Start

1. Check logs: `sudo journalctl -u hydropulse.service -n 50`
2. Validate configuration: `sudo -u hydropulse /opt/hydropulse/venv/bin/python -c "from backend.config import load_config; load_config()"`
3. Verify environment file permissions: `ls -la /etc/hydropulse/hydropulse.env`
4. Ensure Python 3.11 venv is activated and dependencies installed

### CORS Errors in Frontend

1. Verify `HYDROPULSE_CORS_ORIGINS` matches the exact frontend origin
2. Check that the placeholder `https://frontend.example.invalid` has been replaced
3. Restart the service after updating `/etc/hydropulse/hydropulse.env`

### Health Check Returns 503

1. Wait for sensitivity matrix initialization (may take up to 60 seconds on cold start)
2. Check logs for WNTR simulation errors
3. Verify `backend/bangalore_kengeri.inp` exists and is readable

### Permission Denied Errors

1. Verify `/var/lib/hydropulse` is owned by `hydropulse:hydropulse` with mode 750
2. Verify `/etc/hydropulse/hydropulse.env` is owned by `root:root` with mode 600
3. Check that the service user has read access to repository files

---

## 15. OpenRouter Integration (Optional)

When `OPENROUTER_API_KEY` is set, the backend enriches AI suggestions via OpenRouter. If unset or rate-limited, the API returns deterministic engineering rationale without errors. OpenRouter is the sole external AI integration; direct provider URLs are blocked at startup.

OpenRouter configuration in `/etc/hydropulse/hydropulse.env`:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
HYDROPULSE_OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
HYDROPULSE_OPENROUTER_TIMEOUT_S=4.0
HYDROPULSE_OPENROUTER_CONNECT_TIMEOUT_S=2.0
HYDROPULSE_OPENROUTER_MAX_RETRIES=1
```

---

## 16. Performance Notes

- **Single-worker process** required due to in-memory hydraulic state and telemetry ring buffers
- **Warm startup** (cached sensitivity matrix): ~371 ms
- **Cold startup** (matrix build): ~10.5 seconds
- **Peak memory**: ~611 MB RSS
- **Latency targets**: Health/ready < 50 ms, network fetch < 100 ms, simulations < 1,000 ms

These metrics are based on the Kengeri DMA network (129 junctions, 168 pipes, 5 sensors).

---

## 17. Migration from Container Deployment

If migrating from a previous container-based deployment:

1. Export container state: `docker cp hydropulse-backend:/app/backend/.state /tmp/hydropulse-state-backup`
2. Stop and remove containers: `docker stop hydropulse-backend && docker rm hydropulse-backend`
3. Follow this guide to deploy the systemd service
4. Restore state: `sudo cp -r /tmp/hydropulse-state-backup/* /var/lib/hydropulse/`
5. Verify ownership: `sudo chown -R hydropulse:hydropulse /var/lib/hydropulse`
6. Start the service: `sudo systemctl start hydropulse.service`
---

This guide covers the complete deployment lifecycle for HydroPulse on AWS EC2 with systemd management. For questions or issues, consult the repository README or open an issue in the project tracker.
