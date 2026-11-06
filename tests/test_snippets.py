"""Recortes: la ventana del chunk mas parecida a la consulta (para el hook)."""
import recall_hook as hook
from recall.search import SNIPPET_ROWS, Searcher, split_windows


def test_split_windows_respects_width_and_limit():
    text = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(120))
    wins = split_windows(text, 300)
    assert 1 < len(wins) <= SNIPPET_ROWS
    assert "".join(w.replace(" ", "") for w in wins) == text.replace(" ", "")
    short = split_windows("Una frase corta.", 300)
    assert short == ["Una frase corta."]
    # una frase larguisima se corta a lo bruto
    assert all(len(w) <= 400 for w in split_windows("x" * 1500, 400))


def test_search_adds_snippet_only_when_asked(indexed):
    db, _lib, emb = indexed
    s = Searcher(db, emb)
    plain = s.search("error budget burn rate", k=5)
    assert plain and all("snippet" not in h for h in plain)
    hits = s.search("error budget burn rate", k=5, snippet_chars=200, snippet_top=2)
    long_ = [h for h in hits if len(h["text"]) > 200]
    with_snip = [h for h in hits if "snippet" in h]
    assert len({h["path"] for h in with_snip}) <= 2
    for h in with_snip:
        assert len(h["snippet"]) <= 250 and h["snippet_hits"] >= 0
    assert with_snip or not long_


def test_window_picks_the_part_that_matches_the_query():
    from recall.search import query_terms, window_score

    text = ("Intro about teams and history of the company. " * 12
            + "An error budget is the allowed unreliability; burn rate alerts fire when it is spent fast. "
            + "Closing remarks about culture. " * 12)
    wins = split_windows(text, 300)
    terms = query_terms(["que es un error budget y como se alerta por burn rate"])
    best = max(range(len(wins)), key=lambda i: (window_score(wins[i], terms), -i))
    assert "error budget" in wins[best]
    assert window_score("nada que ver", terms) == (0, 0)
    assert "error budget" in terms and "que" not in terms


def test_hook_renders_snippet_over_text():
    h = {"book": "SRE", "chapter": "Ch 3", "path": "x.md", "line_start": 1, "line_end": 9, "cos": 0.8,
         "text": "INICIO irrelevante " * 50, "snippet": "la parte pertinente"}
    out = hook.render([h], 700)
    assert "la parte pertinente" in out and "INICIO" not in out
    out = hook.render([{**h, "snippet": None}], 700)
    assert "INICIO" in out


def test_service_passes_snippet_params(service):
    import json
    import urllib.request

    body = json.dumps({"queries": ["error budget burn rate"], "k": 5, "snippet_chars": 200}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{service['port']}/search", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {service['token']}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as r:
        hits = json.load(r)["hits"]
    assert hits
