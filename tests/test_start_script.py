"""start-recall.ps1: fail-open, silencioso, no duplica un servicio sano."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "start-recall.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="sin pwsh")


def _run(home, **extra):
    env = {**os.environ, "RECALL_HOME": str(home)}
    args = [PWSH, "-NoProfile", "-File", str(SCRIPT)]
    for k, v in extra.items():
        args += [f"-{k}", str(v)]
    return subprocess.run(args, capture_output=True, timeout=120, env=env)


def test_no_index_exits_zero_silently(home):
    r = _run(home)
    assert r.returncode == 0 and r.stdout.strip() == b"", r
    assert (home / "start.log").is_file(), r
    assert "sin indice" in (home / "start.log").read_text(encoding="utf-8")


def test_missing_venv_exits_zero(home, tmp_path):
    (home / "index.sqlite3").write_bytes(b"")
    r = _run(home, RecallRoot=tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == b""
    assert "no existe" in (home / "start.log").read_text(encoding="utf-8")


def test_start_log_rotates_over_1mib(home):
    (home / "start.log").write_text("viejo\n" * 200_000, encoding="utf-8")  # ~1.2 MB
    r = _run(home)
    assert r.returncode == 0
    assert (home / "start.log.1").read_text(encoding="utf-8").startswith("viejo")
    assert "sin indice" in (home / "start.log").read_text(encoding="utf-8")


def test_healthy_service_is_left_alone(home, service):
    r = _run(home)
    assert r.returncode == 0 and r.stdout.strip() == b""
    log = home / "start.log"
    assert not log.exists() or "lanzado" not in log.read_text(encoding="utf-8")
