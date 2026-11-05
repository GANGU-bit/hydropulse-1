Exit code: 0
Wall time: 5.6 seconds
Output:
# HydroPulse Environment Setup (PowerShell)
$ErrorActionPreference = "Stop"

Write-Host "=== HydroPulse Environment Setup ==="

$pythonCommand = $null
$pythonPrefixArgs = @()

if ($env:HYDROPULSE_PYTHON) {
    $candidate = Get-Command $env:HYDROPULSE_PYTHON -ErrorAction SilentlyContinue
    if ($candidate) {
        & $candidate.Source -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $candidate.Source
        }
    }
}

if (-not $pythonCommand -and (Get-Command py -ErrorAction SilentlyContinue)) {
    foreach ($version in @("3.11", "3.12", "3.10")) {
        & py "-$version" -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = "py"
            $pythonPrefixArgs = @("-$version")
            break
        }
    }
}

if (-not $pythonCommand) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) {
        & $candidate.Source -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $candidate.Source
        }
    }
}

if (-not $pythonCommand) {
    throw "HydroPulse requires Python 3.10, 3.11, or 3.12. Set HYDROPULSE_PYTHON to a supported interpreter if needed."
}

Write-Host "Using Python: $(& $pythonCommand @pythonPrefixArgs --version)"

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment in .venv..."
    & $pythonCommand @pythonPrefixArgs -m venv .venv
}

Write-Host "Installing Python dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (Test-Path "frontend/package.json") {
    Write-Host "Installing npm dependencies in frontend..."
    Push-Location frontend
    npm install
    Pop-Location
}

Write-Host "=== Setup complete! ==="

