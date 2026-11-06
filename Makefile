# Windows-first project: these targets assume PowerShell or Git Bash with uv on PATH.
PY := .venv/Scripts/python.exe

.PHONY: install test test-model eval verify check

install:
	uv venv --python 3.12
	uv pip sync requirements.lock

test:
	$(PY) -I -m pytest -q

test-model:
	$(PY) -I -m pytest -q -m model

eval:
	$(PY) -I experiments/evaluate.py

verify:
	$(PY) -I tools/verify_index.py --sample 30

check: test
