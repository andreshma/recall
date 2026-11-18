"""DirectML query sessions run at ONE input shape (marker ``model``, ADR 0010).

A DirectML session is fast only for the first shape it runs; after a second
shape every call took 1-3.6 s. Whatever the query length, the warm-up or the
windows, the session must only ever see ``query_shape``, and padding to that
width must not change the vectors.
"""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.model

PROMPTS = Path(__file__).parent / "data" / "prompts_long_20.json"


@pytest.fixture(scope="module")
def cfg():
    from recall import config

    c = copy.deepcopy(config.load())
    c.setdefault("search", {})["query_window_tokens"] = 32
    c["model"]["query_providers"] = list(c["model"]["index_providers"])  # DirectML first
    try:
        from recall.embedder import from_config

        emb = from_config(c, "query")
    except FileNotFoundError:
        pytest.skip("no local model")
    if emb.query_shape is None:
        pytest.skip("query session is not on DirectML")
    return c


def test_every_query_runs_at_the_pinned_shape(cfg):
    from recall.embedder import format_query, from_config, query_windows
    from recall.service import warm_up

    emb = from_config(cfg, "query")
    assert emb.query_shape == (1, 32)
    shapes = []
    real = emb._sess

    class Spy:
        def run(self, names, feed):
            shapes.append(feed["input_ids"].shape)
            return real.run(names, feed)

        def __getattr__(self, name):
            return getattr(real, name)

    emb._sess = Spy()

    class S:
        pass

    S.emb = emb
    warm_up(S)
    prompts = [p["q"] for p in json.loads(PROMPTS.read_text(encoding="utf-8"))]
    wins = [format_query(emb, w) for p in prompts[:5] for w in query_windows(emb, p[:1000], 32, 6)]
    emb.embed(wins, "query")
    emb.embed([format_query(emb, "bounded context")], "query")
    emb.embed([format_query(emb, prompts[0])], "query")  # longer than the window: truncated, same shape
    assert set(shapes) == {(1, 32)}


def test_padding_to_the_pinned_width_keeps_the_vectors(cfg):
    from recall.embedder import format_query, from_config, query_windows

    dml = from_config(cfg, "query")
    cpu_cfg = copy.deepcopy(cfg)
    cpu_cfg["model"]["query_providers"] = ["CPUExecutionProvider"]
    cpu = from_config(cpu_cfg, "query")
    assert cpu.query_shape is None
    prompts = [p["q"] for p in json.loads(PROMPTS.read_text(encoding="utf-8"))]
    wins = [format_query(dml, w) for p in prompts[:5] for w in query_windows(dml, p[:1000], 32, 6)]
    wins.append(format_query(dml, "saga"))
    diff = np.abs(dml.embed(wins, "query") - cpu.embed(wins, "query")).max()
    assert diff < 1e-4
