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


def test_threshold_counts_use_the_highest_hit_not_the_first(tmp_path):
    # The hook injects when any hit clears the threshold; "best" is only the
    # first hit of the hybrid ranking. Counting by "best" undercounted
    # would-inject prompts by about half on real logs.
    log = tmp_path / "shadow.log"
    rows = [
        {
            "ts": "2026-11-18T10:00:00",
            "decision": "would-inject",
            "best": 0.70,
            "scores": [0.70, 0.76, 0.72],
            "ms": 900,
        },
        {"ts": "2026-11-18T11:00:00", "decision": "below-threshold", "best": 0.73, "scores": [0.73, 0.71], "ms": 800},
        {"ts": "2026-11-18T12:00:00", "decision": "service-unavailable", "err": "TimeoutError", "ms": 1521},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s = sr.summarize(sr.read_rows(log))
    assert s["searched"] == 2
    assert s["would_inject_at"][0.75] == 1 and s["would_inject_at"][0.74] == 1 and s["would_inject_at"][0.72] == 2
    assert s["latency_ms"]["max"] == 900  # the timeout has no score and stays out of the latency


def test_top_score_falls_back_to_best():
    assert sr.top_score({"best": 0.61}) == 0.61
    assert sr.top_score({"best": 0.5, "scores": [0.5, 0.8]}) == 0.8
    assert sr.top_score({"best": None, "scores": []}) is None
    assert sr.top_score({"decision": "skip-trivial"}) is None


def test_empty_log(tmp_path):
    s = sr.summarize(sr.read_rows(tmp_path / "shadow.log"))
    assert s["total"] == 0 and s["latency_ms"] is None
