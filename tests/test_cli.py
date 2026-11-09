"""End-to-end CLI with the test embedder (no real model or index).

Covers what used to be exercised only by the ``model`` suite: index, status, search
(no service, in process) and eval (L7 of the 2026-11-06 audit).
"""

import json

import pytest

from recall import cli, config
from recall.embedder import HashEmbedder


@pytest.fixture
def fake_model(monkeypatch):
    """``from_config`` returns the test embedder for any role."""
    import recall.embedder as E

    monkeypatch.setattr(E, "from_config", lambda cfg, role: HashEmbedder())


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def test_index_status_search_eval(home, library, fake_model, capsys, tmp_path):
    assert cli.main(["index", "--root", str(library)]) == 0
    rep = _json_out(capsys)
    assert rep["added"] > 0 and rep["chunks_embedded"] > 0 and not rep["errors"]

    # Second pass: incremental, nothing is embedded again.
    assert cli.main(["index", "--root", str(library)]) == 0
    assert _json_out(capsys)["chunks_embedded"] == 0

    assert cli.main(["status"]) == 0
    st = _json_out(capsys)
    assert st["chunks"] > 0 and st["books"] == 3 and "unavailable" in st["service"]
    assert st["model"]["model"] == "hash-test"

    assert cli.main(["search", "cono de incertidumbre", "-k", "3", "--text"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[1] cos=") and "libro-es" in out

    q = [
        {
            "id": "T1",
            "type": "book",
            "qlang": "es",
            "dlang": "es",
            "q": ["cono de incertidumbre"],
            "rel": {"path": [r"libro-es[\\/]01-capitulo-1\.md"]},
        },
        {"id": "T2", "type": "otro", "qlang": "en", "dlang": "en", "q": "x", "rel": {"path": []}},
    ]
    qf, outf = tmp_path / "q.json", tmp_path / "res.json"
    qf.write_text(json.dumps(q), encoding="utf-8")
    assert cli.main(["eval", "--queries", str(qf), "--out", str(outf)]) == 0
    printed = capsys.readouterr().out
    assert "TOTAL" in printed and "threshold" in printed
    res = json.loads(outf.read_text(encoding="utf-8"))
    assert res["groups"]["TOTAL"]["n"] == 1  # only the ones of type libro
    assert res["groups"]["TOTAL"]["r5"] == 1.0
    assert res["calibration"]["neg"] and res["calibration"]["pos"]


def test_index_flags_choose_providers(home, library, monkeypatch, capsys):
    import recall.embedder as E

    seen = {}

    def fake(cfg, role):
        seen["providers"] = cfg["model"]["index_providers"]
        return HashEmbedder()

    monkeypatch.setattr(E, "from_config", fake)
    assert cli.main(["index", "--root", str(library), "--cpu", "--limit-files", "1"]) == 0
    assert seen["providers"] == ["CPUExecutionProvider"]
    assert _json_out(capsys)["added"] == 1
    cli.main(["index", "--root", str(library), "--dml-device", "1"])
    assert seen["providers"][0] == "DmlExecutionProvider:1"


def test_status_without_index(home, capsys):
    assert cli.main(["status"]) == 0
    st = _json_out(capsys)
    assert "chunks" not in st and st["db"] == str(config.db_path())


def test_unknown_command_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["nope"])
    assert e.value.code == 2


def test_eval_without_query_set_fails_loudly(home, monkeypatch, tmp_path, capsys):
    """F-10: a wheel has no tests/data; eval must say so instead of a traceback."""
    monkeypatch.setattr(cli, "DEFAULT_QUERIES", tmp_path / "absent.json")
    assert cli.main(["eval"]) == 2
    assert "--queries" in capsys.readouterr().err
