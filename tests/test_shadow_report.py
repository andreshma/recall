import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import shadow_report as sr


def test_summarize_reads_rotations_and_counts(tmp_path):
    log = tmp_path / "shadow.log"
    rows = [
        {"ts": "2026-11-05T10:00:00", "decision": "below-threshold", "best": 0.40, "ms": 300, "paths": []},
        {"ts": "2026-11-05T11:00:00", "decision": "would-inject", "best": 0.61, "ms": 500, "paths": ["a.md"]},
        {"ts": "2026-11-05T12:00:00", "decision": "trivial"},
    ]
    (tmp_path / "shadow.log.1").write_text(json.dumps(rows[0]) + "\nbasura\n", encoding="utf-8")
    log.write_text("\n".join(json.dumps(r) for r in rows[1:]) + "\n", encoding="utf-8")
    s = sr.summarize(sr.read_rows(log))
    assert s["total"] == 3 and s["searched"] == 2
    assert s["decisions"]["would-inject"] == 1
    assert s["would_inject_at"][0.58] == 1 and s["would_inject_at"][0.50] == 1 and s["would_inject_at"][0.62] == 0
    assert s["top_paths"] == [("a.md", 1)]
    assert sr.summarize(sr.read_rows(log, since="2026-11-05T10:30"))["total"] == 2


def test_empty_log(tmp_path):
    s = sr.summarize(sr.read_rows(tmp_path / "shadow.log"))
    assert s["total"] == 0 and s["latency_ms"] is None
