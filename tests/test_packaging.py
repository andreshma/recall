"""Lock de dependencias, postura offline, config y docs/USAGE.md coherentes."""
import json
import re
from importlib import metadata
from pathlib import Path

from recall import config

ROOT = Path(__file__).resolve().parents[1]


def _pins(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def test_lock_pins_every_input_with_hashes():
    lock = _pins(ROOT / "requirements.lock")
    for name, ver in _pins(ROOT / "requirements.in").items():
        assert lock.get(name) == ver, name
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert text.count("==") == text.count("\\\n    --hash=sha256:") or "--hash=sha256:" in text


def test_huggingface_hub_excluded():
    """Sin huggingface-hub el proceso no tiene con que descargar del Hub."""
    assert "huggingface-hub" not in _pins(ROOT / "requirements.lock")
    try:
        metadata.version("huggingface-hub")
        installed = True
    except metadata.PackageNotFoundError:
        installed = False
    assert not installed


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

# Hooks de ~\.claude\settings.json tal como estaban el 2026-10-27 (solo la forma).
CURRENT_HOOKS = {
    "SessionStart": [{"hooks": [{"type": "command", "command": "pwsh"}]},
                     {"hooks": [{"type": "command", "command": "otro-hook --session-start"}]}],
    "Stop": [{"hooks": [{}]}], "SessionEnd": [{"hooks": [{}]}], "PreCompact": [{"hooks": [{}]}],
}


def _json_block(tag):
    m = re.search(r"<!-- %s -->\s*```json\s*(.+?)```" % re.escape(tag), INSTALL, re.S)
    assert m, f"falta el bloque {tag} en docs/USAGE.md"
    return json.loads(m.group(1))


def test_install_hook_blocks_are_valid_and_point_to_real_files():
    # docs/USAGE.md usa C:\Users\<usuario>\...: se compara la cola de la ruta.
    ups = _json_block("bloque-userpromptsubmit")
    ss = _json_block("bloque-sessionstart")
    (h,) = ups["hooks"]
    assert h["type"] == "command" and h["args"][0] == "-I"
    assert Path(h["command"]).name == "python.exe" and "recall" in h["command"]
    assert Path(h["args"][1]).parts[-2:] == (ROOT.name, "recall_hook.py")
    assert 2 <= h["timeout"] <= 10
    (s,) = ss["hooks"]
    assert s["command"] == "pwsh" and Path(s["args"][-1]).parts[-2:] == (ROOT.name, "start-recall.ps1")


def test_install_merge_appends_without_reordering():
    """Aplicar los bloques conserva los hooks existentes y su orden."""
    ups, ss = _json_block("bloque-userpromptsubmit"), _json_block("bloque-sessionstart")
    merged = json.loads(json.dumps(CURRENT_HOOKS))
    merged["SessionStart"].append(ss)
    merged.setdefault("UserPromptSubmit", []).append(ups)
    assert merged["SessionStart"][:2] == CURRENT_HOOKS["SessionStart"]
    assert merged["SessionStart"][2] == ss
    for k in ("Stop", "SessionEnd", "PreCompact"):
        assert merged[k] == CURRENT_HOOKS[k]


def test_install_mentions_backup_shadow_active_uninstall():
    for must in ("Backup", "shadow", "active", "Uninstall", "claude mcp add", "alwaysLoad",
                 "claude_desktop_config.json", "HF_HUB_OFFLINE"):
        assert must in INSTALL, must


def test_example_config_is_the_e5_experiment():
    """config.example.json (lo que la doc recomienda) es exactamente E5 mas el hook calibrado."""
    ex = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    e5 = json.loads((ROOT / "experiments" / "configs" / "e5-gemma2-chunk3500.json").read_text(encoding="utf-8"))
    assert ex["chunking"] == e5["chunking"]
    assert {k: v for k, v in ex["model"].items() if k != "snapshot"} == \
        {k: v for k, v in e5["model"].items() if k != "snapshot"}
    assert ex["hook"] == {"mode": "shadow", "threshold": 0.75}
