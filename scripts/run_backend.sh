#!/usr/bin/env bash
set -e

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
fi

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

