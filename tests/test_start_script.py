"""start-recall.ps1: fail-open, silent, does not duplicate a healthy service.

Runs under pwsh 7 and under Windows PowerShell 5.1 when they are installed
(F-06: the script once used ``Invoke-RestMethod -NoProxy``, which 5.1 lacks).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "start-recall.ps1"
FAKE = Path(__file__).resolve().parent / "_fake_service.py"
SHELLS = [s for s in ("pwsh", "powershell") if shutil.which(s)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="no PowerShell found")


@pytest.fixture(params=SHELLS)
def shell(request):
    return shutil.which(request.param)


def _run(shell, home, **extra):
    env = {**os.environ, "RECALL_HOME": str(home)}
    args = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
    for k, v in extra.items():
        args += [f"-{k}", str(v)]
    return subprocess.run(args, capture_output=True, timeout=120, env=env)


def _log(home):
    p = home / "start.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_no_index_exits_zero_silently(shell, home):
    r = _run(shell, home)
    assert r.returncode == 0 and r.stdout.strip() == b"", r
    assert (home / "start.log").is_file(), r
    assert "no index" in _log(home)


def test_missing_venv_exits_zero(shell, home, tmp_path):
    (home / "index.sqlite3").write_bytes(b"")
    r = _run(shell, home, RecallRoot=tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == b""
    assert "missing" in _log(home)


def test_start_log_rotates_over_1mib(shell, home):
    (home / "start.log").write_text("viejo\n" * 200_000, encoding="utf-8")  # ~1.2 MB
    r = _run(shell, home)
    assert r.returncode == 0
    assert (home / "start.log.1").read_text(encoding="utf-8").startswith("viejo")
    assert "no index" in _log(home)


def test_healthy_service_is_left_alone(shell, home, service):
    """The health check itself works: nothing is logged, in 5.1 as in pwsh (F-06)."""
    r = _run(shell, home)
    assert r.returncode == 0 and r.stdout.strip() == b""
    assert _log(home) == "", _log(home)


# -- stand-in service processes ------------------------------------------------------
@pytest.fixture
def fake_service(home, tmp_path):
    """Start ``_fake_service.py`` in ``mode`` and point ``service.json`` at it."""
    procs = []

    def start(mode, token="T" * 43):
        record = tmp_path / f"requests-{mode}.txt"
        record.write_text("", encoding="utf-8")
        # The base interpreter, not a venv launcher: the pid must be the server's own.
        exe = getattr(sys, "_base_executable", sys.executable)
        p = subprocess.Popen([exe, "-I", str(FAKE), mode, token, str(record)], stdout=subprocess.PIPE)
        procs.append(p)
        port = int(p.stdout.readline())
        svc = {"port": port, "token": token, "pid": p.pid, "started": "x"}
        (home / "service.json").write_text(json.dumps(svc), encoding="utf-8")
        return p, record

    yield start
    for p in procs:
        p.kill()
        p.wait(10)


def test_impostor_on_the_port_gets_no_token_and_is_not_killed(shell, home, fake_service):
    """ADV-2: a stale service.json whose port another process now holds."""
    proc, record = fake_service("impostor")
    r = _run(shell, home)
    assert r.returncode == 0 and r.stdout.strip() == b""
    seen = record.read_text(encoding="utf-8").splitlines()
    assert len(seen) == 1 and seen[0].startswith("GET /proof?nonce=") and seen[0].endswith("auth="), seen
    assert proc.poll() is None  # not killed
    assert "did not prove the token" in _log(home)
    assert not (home / "service.json").exists()


@pytest.fixture
def stub_root(tmp_path):
    """A RecallRoot whose ``.venv`` runs a stub ``recall`` that records how it was launched."""
    base = Path(getattr(sys, "_base_executable", sys.executable)).parent
    launcher = Path(sys.executable).with_name("pythonw.exe")
    if not (base / "pythonw.exe").exists() or not launcher.exists() or sys.prefix == sys.base_prefix:
        pytest.skip("needs a Windows venv with pythonw.exe")
    root = tmp_path / "root"
    scripts = root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(launcher, scripts / "pythonw.exe")
    (root / ".venv" / "pyvenv.cfg").write_text(f"home = {base}\ninclude-system-site-packages = false\n")
    pkg = root / ".venv" / "Lib" / "site-packages" / "recall"
    pkg.mkdir(parents=True)
    out = tmp_path / "launched.json"
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(
        "import json, os\n"
        "def main(argv=None):\n"
        f"    with open({str(out)!r}, 'w') as f:\n"
        "        json.dump({'argv': argv, 'home': os.environ.get('RECALL_HOME')}, f)\n"
        "    return 0\n"
    )
    (pkg / "__main__.py").write_text("import sys\nfrom recall.cli import main\nsys.exit(main(sys.argv[1:]))\n")
    return root, out


def _wait_for(path, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.stat().st_size:
            return json.loads(path.read_text())
        time.sleep(0.2)
    raise AssertionError(f"{path} was not written")


def test_degraded_service_is_restarted_with_the_state_dir(shell, home, fake_service, stub_root):
    """503 -> the proven pid is killed and a new service is launched on RECALL_HOME (F-06)."""
    root, out = stub_root
    (home / "index.sqlite3").write_bytes(b"")
    proc, record = fake_service("degraded")
    r = _run(shell, home, RecallRoot=root)
    assert r.returncode == 0 and r.stdout.strip() == b""
    assert proc.wait(10) is not None  # killed
    assert "service degraded (503" in _log(home) and "launched recall serve" in _log(home), _log(home)
    seen = record.read_text(encoding="utf-8").splitlines()
    assert seen[0].startswith("GET /proof?nonce=") and seen[0].endswith("auth="), seen
    assert seen[1] == f"GET /health HTTP/1.1 | auth=Bearer {'T' * 43}", seen
    launched = _wait_for(out)
    assert launched["argv"] == ["serve"]
    # Win32_Process.Create does not pass this process's environment on.
    assert Path(launched["home"]) == home
