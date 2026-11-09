"""Query windows for long prompts (measured on 2026-11-06).

With quantized embeddinggemma-2 on DirectML, more than 32 tokens per call
costs seconds; ``query_windows`` splits the prompt and ``Searcher.search`` merges
the windows. See ``docs/adr`` and ``embedder.query_windows``.
"""

import time

from recall.embedder import HashEmbedder, query_windows
from recall.search import Searcher


class WordCounter:
    """Counts one token per word (no prefix): easy to reason about in tests."""

    def count_query_tokens(self, q: str) -> int:
        return len(q.split())


def test_short_query_is_left_alone():
    assert query_windows(WordCounter(), "a b c", 5, 4) == ["a b c"]
    assert query_windows(WordCounter(), "a b c d e f", 0, 4) == ["a b c d e f"]  # 0 = off
    assert query_windows(object(), "a b c d e f", 2, 4) == ["a b c d e f"]  # cannot count


def test_windows_without_and_with_overlap():
    words = " ".join(f"w{i}" for i in range(10))
    assert query_windows(WordCounter(), words, 4, 9) == ["w0 w1 w2 w3", "w4 w5 w6 w7", "w8 w9"]
    assert query_windows(WordCounter(), words, 4, 9, overlap=1) == ["w0 w1 w2 w3", "w3 w4 w5 w6", "w6 w7 w8 w9"]


def test_window_cap_and_oversized_word():
    words = " ".join(f"w{i}" for i in range(20))
    assert len(query_windows(WordCounter(), words, 4, 2)) == 2

    class Url(WordCounter):
        def count_query_tokens(self, q):
            return sum(40 if w.startswith("http") else 1 for w in q.split())

    out = query_windows(Url(), "a b http://x/very/long c d e", 4, 9)
    assert all("http" not in w for w in out) and " ".join(out).split() == ["a", "b", "c", "d", "e"]


class CountingHash(HashEmbedder):
    """HashEmbedder that can count tokens (one word = one token)."""

    def count_query_tokens(self, q: str) -> int:
        return len(q.split())


def _searcher(indexed, **cfg):
    db, _lib, _ = indexed
    emb = CountingHash()
    return Searcher(db, emb, {"query_window_tokens": 6, "query_max_windows": 6, "query_window_overlap": 0, **cfg}), emb


def test_long_prompt_finds_topic_buried_after_context(indexed):
    s, emb = _searcher(indexed)
    prompt = (
        "tengo una reunion con el cliente manana y quiero preparar bien la propuesta antes "
        "explicame el cono de incertidumbre en la estimacion"
    )
    hits = s.search(prompt, k=3)
    assert emb.calls > 1  # it was split into windows, one call each
    assert hits and "incertidumbre" in hits[0]["text"].lower()


def test_deadline_stops_embedding_but_keeps_the_first_window(indexed):
    s, emb = _searcher(indexed)
    emb.calls = 0
    s.search(
        "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece", k=3, deadline=time.perf_counter() - 1
    )
    assert emb.calls == 1


def test_windows_are_interleaved_across_queries(indexed):
    s, emb = _searcher(indexed, query_max_windows=2)
    seen = []
    orig = emb.embed

    def spy(texts, kind):
        seen.append(texts[0])
        return orig(texts, kind)

    emb.embed = spy
    s.search(["a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 a11 a12 a13", "b1 b2 b3"], k=3)
    assert any("b1" in t for t in seen)  # the second query is not left without a window
