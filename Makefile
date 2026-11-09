# Windows-first project: these targets assume PowerShell or Git Bash with uv on PATH.
# `make install` is the README quickstart: locked environment, then the package
# itself (editable, so `python -I -m recall` and the hooks find it).
PY := .venv/Scripts/python.exe

.PHONY: install dev test test-model lint eval verify check

install:
	uv venv --python 3.12 .venv
	uv pip sync --require-hashes --python $(PY) requirements.lock
	uv pip install --no-deps --no-build-isolation --python $(PY) -e .
	$(PY) -I -m recall --help

dev: install
	uv pip install --require-hashes --no-deps --python $(PY) -r requirements-dev.lock

test:
	$(PY) -I -m pytest -q

test-model:
	$(PY) -I -m pytest -q -m model

lint:
	$(PY) -m ruff format --check .
	$(PY) -m ruff check .
	$(PY) -m mypy --platform win32

eval:
	$(PY) -I experiments/evaluate.py

verify:
	$(PY) -I tools/verify_index.py --sample 30

check: lint test
