# Run HydroPulse Backend (Dev / Prod aware)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$EnvMode = if ($env:HYDROPULSE_ENV) { $env:HYDROPULSE_ENV } else { "dev" }
$BindHost = if ($env:HYDROPULSE_HOST) { $env:HYDROPULSE_HOST } else { "0.0.0.0" }
$Port = if ($env:HYDROPULSE_PORT) { $env:HYDROPULSE_PORT } else { "8000" }

& .\.venv\Scripts\python.exe -c "from backend.config import load_config; load_config()"

if ($EnvMode -eq "prod" -or $EnvMode -eq "production") {
    & .\.venv\Scripts\uvicorn.exe backend.main:app --host $BindHost --port $Port --workers 1
} else {
    & .\.venv\Scripts\uvicorn.exe backend.main:app --host $BindHost --port $Port --reload
}
