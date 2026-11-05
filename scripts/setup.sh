#!/usr/bin/env bash
set -e

echo "=== HydroPulse Environment Setup ==="

# Select a supported interpreter. WNTR wheels are not available for every new
# Python release, and SOURCE_OF_TRUTH.md permits Python 3.10--3.12 only.
PY_CMD=()
if command -v py &>/dev/null; then
    for version in 3.11 3.12 3.10; do
        if py "-$version" -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))" &>/dev/null; then
            PY_CMD=(py "-$version")
            break
        fi
    done
fi

if [ ${#PY_CMD[@]} -eq 0 ]; then
    for candidate in python3.11 python3.12 python3.10 python3 python; do
        if command -v "$candidate" &>/dev/null && "$candidate" -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))" &>/dev/null; then
            PY_CMD=("$candidate")
            break
        fi
    done
fi

if [ ${#PY_CMD[@]} -eq 0 ]; then
    echo "Error: HydroPulse requires Python 3.10, 3.11, or 3.12."
    exit 1
fi

echo "Using Python: $(${PY_CMD[@]} --version)"

# Create virtualenv if not present
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    "${PY_CMD[@]}" -m venv .venv
fi

# Activate virtualenv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
fi

# Upgrade pip and install requirements
echo "Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Setup frontend if package.json exists
if [ -f "frontend/package.json" ]; then
    echo "Found frontend/package.json. Installing npm packages..."
    cd frontend && npm install && cd ..
fi

echo "=== Setup complete! ==="

