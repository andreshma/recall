"""Fixtures: biblioteca minima en tmp, embedder determinista y servicio en hilo.

Ningun test unitario carga el modelo real ni toca ``%LOCALAPPDATA%\\recall``:
``RECALL_HOME`` apunta siempre a un tmp. Solo ``test_regression.py`` (marca
``model``) usa el indice y el modelo reales.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from recall.embedder import HashEmbedder  # noqa: E402

DDD = """# Learning DDD (Khononov) — Chapter 3. Managing Domain Complexity

> Source: *Learning DDD (Khononov)*, Chapter 3

# Chapter 3. Managing Domain Complexity

{p1}

# Bounded Contexts

{p2}

###### Figure 3-2. Bounded contexts in the telemarketing company

{p3}

# Ubiquitous Language

{p4}
"""

SRE = """# Site Reliability Engineering — Chapter 3. Embracing Risk

> Source: *SRE*, Chapter 3

# Error Budgets

{p1}

# Burn Rate Alerting

{p2}
"""

ES_BOOK = """# Libro de Pruebas — Capitulo 1. Estimacion

> Source: prueba

# Cono de incertidumbre

{p1}
"""


def para(words: str, n: int) -> str:
    """Parrafo repitiendo ``words`` hasta ~n caracteres."""
    out = []
    while len(" ".join(out)) < n:
        out.append(words)
    return " ".join(out)


def write_library(root: Path) -> Path:
    """Crea ``root/_md`` con tres libros pequenos y front matter a omitir."""
    md = root / "_md"
    (md / "learning-ddd").mkdir(parents=True)
    (md / "sre-google").mkdir(parents=True)
    (md / "libro-es").mkdir(parents=True)
    (md / "learning-ddd" / "05-chapter-3.md").write_text(DDD.format(
        p1=para("Domain complexity grows when models are inconsistent across departments.", 700),
        p2=para("A bounded context is a boundary within which a ubiquitous language and model apply consistently.", 1400),
        p3=para("Marketing and sales each define the lead entity differently inside their own bounded context.", 600),
        p4=para("The ubiquitous language must be consistent and free of ambiguity for domain experts.", 900),
    ), encoding="utf-8")
    (md / "learning-ddd" / "00-front-matter.md").write_text("# x — Front matter\n\n" + para("praise praise", 400), encoding="utf-8")
    (md / "learning-ddd" / "01-cover.md").write_text("# x — Cover\n\n" + para("cover art", 400), encoding="utf-8")
    (md / "sre-google" / "09-chapter-3.md").write_text(SRE.format(
        p1=para("An error budget is the allowed unreliability of a service under its SLO.", 900),
        p2=para("Alert on error budget burn rate over multiple windows to page on fast burns.", 900),
    ), encoding="utf-8")
    (md / "sre-google" / "13-chapter-7-disaster-recovery.md").write_text(
        "# SRE — Chapter 7. Disaster Recovery\n\n# Recovery\n\n" + para("Disaster recovery testing restores backups regularly.", 600),
        encoding="utf-8")
    (md / "libro-es" / "01-capitulo-1.md").write_text(ES_BOOK.format(
        p1=para("El cono de incertidumbre describe como la estimación se vuelve más precisa con el avance del proyecto.", 800),
    ), encoding="utf-8")
    return md


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """``RECALL_HOME`` aislado en tmp."""
    h = tmp_path / "state"
    h.mkdir()
    monkeypatch.setenv("RECALL_HOME", str(h))
    return h


@pytest.fixture
def library(tmp_path) -> Path:
    """Raiz ``_md`` con la biblioteca minima."""
    return write_library(tmp_path / "books")


@pytest.fixture
def emb() -> HashEmbedder:
    """Embedder determinista sin modelo."""
    return HashEmbedder()


@pytest.fixture
def indexed(home, library, emb):
    """Indice construido sobre la biblioteca minima: (db, library, emb)."""
    from recall import config
    from recall.indexer import Indexer

    Indexer(config.db_path(), emb, quiet=True).run({"books": {"roots": [str(library)]}})
    return config.db_path(), library, emb


@pytest.fixture
def service(indexed):
    """Servicio real (``recall.service.serve``) en un hilo, con HashEmbedder."""
    from recall import config
    from recall.search import Searcher
    from recall.service import serve

    db, _lib, emb = indexed
    searcher = Searcher(db, emb)
    ready, box = threading.Event(), []
    t = threading.Thread(target=serve, kwargs=dict(cfg=config.load(), searcher=searcher, ready=ready, server_box=box),
                         daemon=True)
    t.start()
    assert ready.wait(60), "el servicio no arranco"
    svc = json.loads(config.service_file().read_text(encoding="utf-8"))
    yield svc
    box[0].shutdown()
    t.join(5)
