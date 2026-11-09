"""Dependency lock, offline posture, config and docs/USAGE.md stay consistent."""

import json
import re
import sys
from importlib import metadata
from pathlib import Path, PureWindowsPath

import pytest

from recall import config

ROOT = Path(__file__).resolve().parents[1]
# requirements.lock is resolved for win/amd64 (onnxruntime-directml) and is
# installed with --no-deps. On another OS (e.g. a Linux checkout installed with
# `pip install -e .`) the environment is not the locked one: these two tests
# only make sense on Windows, where the repo's CI runs them.
windows_lock = pytest.mark.skipif(sys.platform != "win32", reason="the lock is win/amd64")


def _pins(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def _unhashed(path):
    """Pinned lines of a lock that are not continued by a ``--hash=sha256:`` line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line
        for i, line in enumerate(lines)
        if re.match(r"^[A-Za-z0-9_.-]+==", line)
        and not (line.rstrip().endswith("\\") and i + 1 < len(lines) and "--hash=sha256:" in lines[i + 1])
    ]


@pytest.mark.parametrize(
    ("src", "lock"), [("requirements.in", "requirements.lock"), ("requirements-dev.in", "requirements-dev.lock")]
)
def test_lock_pins_every_input_with_hashes(src, lock):
    pins, inputs = _pins(ROOT / lock), _pins(ROOT / src)
    assert inputs
    for name, ver in inputs.items():
        assert pins.get(name) == ver, name
    assert not _unhashed(ROOT / lock)


def test_dev_group_matches_dev_requirements():
    """pyproject's dev group equals requirements-dev.in plus the locked pytest."""
    import tomllib

    group = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["dependency-groups"]["dev"]
    declared = dict(g.split("==") for g in group)
    expected = {**_pins(ROOT / "requirements-dev.in"), "pytest": _pins(ROOT / "requirements.in")["pytest"]}
    assert declared == expected


def test_version_is_the_same_everywhere():
    """pyproject, the package, CITATION.cff and the latest CHANGELOG release agree."""
    import tomllib

    import recall

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert recall.__version__ == version
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(version)}$", cff, re.M)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", changelog, re.M)
    assert releases and releases[0] == version


@windows_lock
def test_huggingface_hub_excluded():
    """Without huggingface-hub the process has nothing to download from the Hub with."""
    assert "huggingface-hub" not in _pins(ROOT / "requirements.lock")
    try:
        metadata.version("huggingface-hub")
        installed = True
    except metadata.PackageNotFoundError:
        installed = False
    assert not installed


@windows_lock
def test_installed_versions_match_lock():
    for name, ver in _pins(ROOT / "requirements.lock").items():
        assert metadata.version(name) == ver, name


def test_offline_env_forced_on_import():
    import os

    import recall  # noqa: F401

    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_config_merge_and_bad_file(home):
    (home / "config.json").write_text(json.dumps({"hook": {"mode": "active"}}), encoding="utf-8")
    c = config.load()
    assert c["hook"]["mode"] == "active" and c["hook"]["threshold"] == config.DEFAULTS["hook"]["threshold"]
    (home / "config.json").write_text("{roto", encoding="utf-8")
    assert config.load()["hook"]["mode"] == "shadow"


def test_hook_defaults_match_package_defaults():
    import recall_hook

    for k, v in recall_hook.DEFAULT_HOOK.items():
        assert config.DEFAULTS["hook"][k] == v, k


# -- docs/USAGE.md ----------------------------------------------------------------
USAGE = ROOT / "docs" / "USAGE.md"
INSTALL = USAGE.read_text(encoding="utf-8") if USAGE.exists() else ""

# Hooks from ~\.claude\settings.json as they were on 2026-10-27 (shape only).
CURRENT_HOOKS = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": "pwsh"}]},
        {"hooks": [{"type": "command", "command": "otro-hook --session-start"}]},
    ],
    "Stop": [{"hooks": [{}]}],
    "SessionEnd": [{"hooks": [{}]}],
    "PreCompact": [{"hooks": [{}]}],
}


def _json_block(tag):
    m = re.search(rf"<!-- {re.escape(tag)} -->\s*```json\s*(.+?)```", INSTALL, re.S)
    assert m, f"block {tag} missing from docs/USAGE.md"
    return json.loads(m.group(1))


def test_install_hook_blocks_are_valid_and_point_to_real_files():
    # docs/USAGE.md uses C:\Users\<user>\source\repos\recall and clones into that folder:
    # the tail of the path is compared against that convention, not against the
    # checkout name (in CI the folder is named after the repo).
    ups = _json_block("block-userpromptsubmit")
    ss = _json_block("block-sessionstart")
    (h,) = ups["hooks"]
    assert h["type"] == "command" and h["args"][0] == "-I"
    # Windows paths: PureWindowsPath splits them the same way on any OS.
    assert PureWindowsPath(h["command"]).name == "python.exe" and "recall" in h["command"]
    assert PureWindowsPath(h["args"][1]).parts[-2:] == ("recall", "recall_hook.py")
    assert 2 <= h["timeout"] <= 10
    (s,) = ss["hooks"]
    assert s["command"] == "pwsh" and PureWindowsPath(s["args"][-1]).parts[-2:] == ("recall", "start-recall.ps1")


def test_install_merge_appends_without_reordering():
    """Applying the blocks keeps the existing hooks and their order."""
    ups, ss = _json_block("block-userpromptsubmit"), _json_block("block-sessionstart")
    merged = json.loads(json.dumps(CURRENT_HOOKS))
    merged["SessionStart"].append(ss)
    merged.setdefault("UserPromptSubmit", []).append(ups)
    assert merged["SessionStart"][:2] == CURRENT_HOOKS["SessionStart"]
    assert merged["SessionStart"][2] == ss
    for k in ("Stop", "SessionEnd", "PreCompact"):
        assert merged[k] == CURRENT_HOOKS[k]


def test_install_mentions_backup_shadow_active_uninstall():
    for must in (
        "Backup",
        "shadow",
        "active",
        "Uninstall",
        "claude mcp add",
        "alwaysLoad",
        "claude_desktop_config.json",
        "HF_HUB_OFFLINE",
    ):
        assert must in INSTALL, must


def test_example_config_is_the_e5_experiment():
    """config.example.json (what the docs recommend) is exactly E5 plus the calibrated hook."""
    ex = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    e5 = json.loads((ROOT / "experiments" / "configs" / "e5-gemma2-chunk3500.json").read_text(encoding="utf-8"))
    assert ex["chunking"] == e5["chunking"]
    assert {k: v for k, v in ex["model"].items() if k != "snapshot"} == {
        k: v for k, v in e5["model"].items() if k != "snapshot"
    }
    assert ex["hook"] == {"mode": "shadow", "threshold": 0.75}


def test_wheel_ships_the_book_metadata(tmp_path):
    """F-10: from a wheel, metadata.py looked for data/ outside site-packages and filters matched nothing."""
    import subprocess
    import zipfile

    pytest.importorskip("hatchling")
    r = subprocess.run(
        [sys.executable, "-I", "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    (whl,) = tmp_path.glob("*.whl")
    site = tmp_path / "site"
    with zipfile.ZipFile(whl) as z:
        assert "recall/data/books_meta.json" in z.namelist()
        z.extractall(site)
    code = "import sys; sys.path.insert(0, sys.argv[1]); from recall import metadata; print(len(metadata.load()))"
    out = subprocess.run([sys.executable, "-I", "-c", code, str(site)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and int(out.stdout) >= 100, out.stderr


# -- README quickstart (F-04) ------------------------------------------------------
def test_readme_quickstart_installs_the_package():
    """The block CI runs verbatim installs the lock, then recall itself, then checks it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("readme_block", ROOT / ".github" / "scripts" / "readme_block.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    body = mod.block("quickstart-install", (ROOT / "README.md").read_text(encoding="utf-8"))
    assert body, "README.md lost the <!-- quickstart-install --> block that CI runs"
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    sync = next(i for i, x in enumerate(lines) if "pip sync" in x and "requirements.lock" in x)
    editable = next(i for i, x in enumerate(lines) if "pip install" in x and "-e ." in x)
    assert "--require-hashes" in lines[sync]
    assert "--no-deps" in lines[editable] and "--no-build-isolation" in lines[editable]
    assert sync < editable  # a sync after the editable install would remove recall again
    assert lines[-1].endswith("-I -m recall --help")
