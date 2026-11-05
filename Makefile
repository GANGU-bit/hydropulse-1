Exit code: 0
Wall time: 4.3 seconds
Output:
.PHONY: setup network network-synthetic backend frontend test benchmark smoke fixtures snapshot presets clean

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
UVICORN ?= $(VENV)/bin/uvicorn
PYTEST ?= $(VENV)/bin/pytest

ifeq ($(OS),Windows_NT)
    PYTHON = $(VENV)/Scripts/python.exe
    UVICORN = $(VENV)/Scripts/uvicorn.exe
    PYTEST = $(VENV)/Scripts/pytest.exe
endif

setup:
	@bash scripts/setup.sh || powershell -ExecutionPolicy Bypass -File scripts/setup.ps1

network:
	$(PYTHON) backend/generate_bangalore_dma.py

network-synthetic:
	$(PYTHON) backend/generate_bangalore_dma.py --source synthetic

backend:
	$(UVICORN) backend.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm run dev -- --host

test:
	$(PYTEST) -q backend/tests tests

benchmark:
	$(PYTHON) backend/benchmark.py

smoke:
	@bash scripts/smoke_test.sh || powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1

fixtures:
	$(PYTHON) data_utils/make_mock_fixtures.py

presets:
	$(PYTHON) data_utils/build_presets.py

snapshot:
	$(PYTHON) data_utils/snapshot_fixtures.py

clean:
	 rm -rf __pycache__ data_utils/__pycache__ backend/__pycache__ tests/__pycache__ .pytest_cache

