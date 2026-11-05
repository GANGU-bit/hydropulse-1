PY ?= .venv/bin/python

.PHONY: setup network network-synthetic backend test benchmark

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

network:
	$(PY) backend/generate_bangalore_dma.py

network-synthetic:
	$(PY) backend/generate_bangalore_dma.py --source synthetic

backend:
	$(PY) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PY) -m pytest -q backend/tests $(wildcard tests)

benchmark:
	$(PY) backend/benchmark.py
