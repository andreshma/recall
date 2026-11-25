"""Hub prior (ADR 0011): per-chunk closeness to generic prompts, its cache,
the ``hub`` field on hits and the hook's adjusted threshold."""

import io
import json

import numpy as np

import recall_hook as hook
from recall import hub
from recall.search import Searcher


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def test_prior_flags_the_chunk_closest_to_the_reference_prompts():
    V = _unit(np.eye(4)[:3])  # three orthogonal chunks
    generic = _unit([[0.9, 0.1, 0, 0]])  # every reference prompt leans to chunk 0
    ref = [generic, generic, _unit([[0.8, 0, 0.2, 0]])]
    h = hub.prior(V, ref, k=2)
    assert h.dtype == np.float32 and h.shape == (3,)
    assert int(np.argmax(h)) == 0 and h[0] > 0.5
    assert float(np.median(h)) == 0.0  # centered on the median chunk


def test_prior_takes_the_best_window_of_each_prompt():
    V = _unit(np.eye(3))
    two_windows = _unit([[0, 0, 1.0], [1.0, 0, 0]])  # one prompt, two windows
    h = hub.prior(V, [two_windows], k=5)  # k is clamped to the prompts available
    assert h[0] == h[2] > h[1]


def test_prior_of_an_empty_index_or_reference():
    assert hub.prior(np.zeros((0, 4), dtype=np.float32), [_unit([[1, 0, 0, 0]])]).shape == (0,)
    assert not hub.prior(_unit(np.eye(2)), []).any()


def test_packaged_prompts_load():
    prompts = hub.load_prompts()
    assert len(prompts) >= 60 and all(isinstance(p, str) and p.strip() for p in prompts)
    assert len(set(prompts)) == len(prompts)


class CountingEmbedder:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0
        self.identity, self.dim = inner.identity, inner.dim

    def embed(self, texts, kind):
        self.calls += 1
        return self.inner.embed(texts, kind)


def test_reference_is_cached_per_model_prompts_and_windows(tmp_path, emb):
    ce = CountingEmbedder(emb)
    prompts = ["revisa el log del hook", "bump the version", "the agent loop never stops"]
    first = hub.reference(ce, {}, tmp_path, prompts)
    assert ce.calls == len(prompts) and (tmp_path / hub.CACHE_NAME).is_file()
    again = hub.reference(ce, {}, tmp_path, prompts)
    assert ce.calls == len(prompts)  # read from the cache
    assert all(np.array_equal(a, b) for a, b in zip(first, again, strict=True))
    hub.reference(ce, {}, tmp_path, [*prompts, "one more"])  # other prompts: new key
    assert ce.calls == 2 * len(prompts) + 1
    hub.reference(ce, {"query_window_tokens": 32}, tmp_path, [*prompts, "one more"])  # other windows
    assert ce.calls == 3 * len(prompts) + 2


def test_corrupt_cache_is_ignored(tmp_path, emb):
    (tmp_path / hub.CACHE_NAME).write_bytes(b"not an npz")
    ref = hub.reference(emb, {}, tmp_path, ["one prompt"])
    assert len(ref) == 1 and ref[0].shape[1] == emb.dim


def test_hits_carry_hub_once_the_reference_is_installed(indexed):
    db, _lib, emb = indexed
    s = Searcher(db, emb)
    q = "bounded context ubiquitous language"
    before = s.search(q, k=3)
    assert before and all("hub" not in h for h in before) and not s.hub_ready
    s.set_hub_reference(hub.reference(emb, s.cfg, None, ["refactor the config loader", "revisa el pipeline"]))
    after = s.search(q, k=3)
    assert s.hub_ready and all(isinstance(h["hub"], float) for h in after)
    assert [h["id"] for h in after] == [h["id"] for h in before]  # the ranking does not use it
    s.load()  # a reload recomputes it for the new snapshot
    assert s.hub is not None and len(s.hub) == s.size


# -- hook ------------------------------------------------------------------
def test_select_applies_the_hub_weight():
    hits = [
        {"path": "hub.md", "cos": 0.80, "hub": 0.08},  # generic chunk: 0.72 adjusted
        {"path": "topic.md", "cos": 0.77, "hub": -0.01},  # 0.78 adjusted
    ]
    assert [h["path"] for h in hook.select(hits, 0.75, 3, hub_weight=1.0)] == ["topic.md"]
    assert [h["path"] for h in hook.select(hits, 0.75, 3, hub_weight=0.0)] == ["hub.md", "topic.md"]
    assert hook.select([{"path": "x.md", "cos": 0.9}], 0.5, 3, hub_weight=1.0) == []  # no hub: never


def test_hub_weight_config_is_validated():
    assert hook.hub_weight_of({}) == 1.0
    for bad in ("x", None, -1, float("nan"), float("inf")):
        assert hook.hub_weight_of({"hub_weight": bad}) == 0.0
    assert hook.hub_weight_of({"hub_weight": "0.5"}) == 0.5


def _run_with_hits(monkeypatch, home, hits, **cfg):
    (home / "config.json").write_text(json.dumps({"hook": {"mode": "active", **cfg}}), encoding="utf-8")
    monkeypatch.setattr(hook, "query_service", lambda *a, **k: hits)
    out = io.BytesIO()
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "explícame qué es un bounded context y el lenguaje ubicuo"}
    assert hook.main(stdin=io.BytesIO(json.dumps(ev).encode()), out=out) == 0
    log = [json.loads(x) for x in (home / "shadow.log").read_text(encoding="utf-8").splitlines()]
    return out.getvalue(), log[-1]


def test_hook_waits_for_the_prior_and_logs_ids_and_adjusted_scores(monkeypatch, home):
    hit = {"id": 7, "path": "a.md", "cos": 0.9, "book": "B", "text": "t"}
    out, entry = _run_with_hits(monkeypatch, home, [hit])
    assert out == b"" and entry["decision"] == "hub-pending" and entry["ids"] == [7]
    out, entry = _run_with_hits(monkeypatch, home, [{**hit, "hub": 0.05}], threshold=0.8)
    assert out and entry["decision"] == "inject" and entry["adj"] == [0.85]
    out, entry = _run_with_hits(monkeypatch, home, [hit], threshold=0.8, hub_weight=0)
    assert out and entry["decision"] == "inject" and "adj" not in entry
