"""What the public repo carries: no private names, paths or source traces (F-01, F-05).

The guard lists the forbidden words by digest, not in clear, so that the test
itself does not publish them: each word (or pair of words) of every tracked
text file is lower-cased, hashed with SHA-256 and compared with
``FORBIDDEN``. To add a term, append ``sha256(term.lower()).hexdigest()[:16]``
(words joined by one space, punctuation dropped).
"""

import hashlib
import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_pages  # noqa: E402

#: Truncated SHA-256 of private terms: the former private library root and
#: converter, a personal name, organization and tool names, and the names of
#: the sites some source files were named after. This file is not scanned (it
#: only holds digests and the sentinel).
FORBIDDEN = frozenset(
    {
        "fdb37f824c5f72ed",
        "bd96131c37d11caa",
        "e109189c7e3d7af2",
        "0940f3d1b061b950",
        "4c21ead5f6293b1c",
        "2597bda1c9684328",
        "14b78ae013e3f72f",
        "9f19007c800f18ed",
        "dd68e1d06a8d0923",
        "ceb194fd65225096",
        "9e156d2e7d2c3d5c",
        "8502178c356c0d3c",  # test sentinel (see test_guard_recognizes_its_terms)
    }
)
TEXT_SUFFIXES = {
    ".md", ".py", ".ps1", ".json", ".jsonl", ".csv", ".txt", ".toml", ".yaml", ".yml", ".cff", ".in", ".lock",
    ".excludes", ".cfg", ".ini", "",
}  # fmt: skip
_WORD = re.compile(r"[a-z0-9]+")


def _digest(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()[:16]


def forbidden_terms(text: str) -> list[str]:
    """Words and word pairs of ``text`` whose digest is in ``FORBIDDEN``."""
    words = _WORD.findall(text.lower())
    grams = set(words) | {f"{a} {b}" for a, b in itertools.pairwise(words)}
    return sorted(g for g in grams if _digest(g) in FORBIDDEN)


def _tracked() -> list[Path]:
    """Files tracked by git; without git (an sdist), everything under the root but .git."""
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout:
            return [ROOT / p for p in r.stdout.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        pass
    return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def test_guard_recognizes_its_terms():
    """The digests really match (a typo in FORBIDDEN would make the guard vacuous)."""
    assert len(FORBIDDEN) == 12
    assert forbidden_terms("Some text with a GUARD-sentinel in it.") == ["guard sentinel"]
    assert forbidden_terms("nothing private here, just bounded contexts and SLOs") == []


def test_no_private_terms_in_tracked_text_files():
    """F-01/F-05: no source-file traces, private paths, personal or organization names."""
    hits = []
    me = Path(__file__).resolve()
    for p in _tracked():
        if p.suffix.lower() not in TEXT_SUFFIXES or not p.is_file() or p.resolve() == me:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if forbidden_terms(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert not hits, hits


def test_books_meta_has_no_source_files_or_evidence_prose():
    meta = json.loads((ROOT / "data" / "books_meta.json").read_text(encoding="utf-8"))
    for bid, m in meta.items():
        assert not {"source_file", "year_evidence"} & set(m), bid
        assert {"title", "author", "format", "year", "domains"} <= set(m), bid


def test_local_sources_map_is_git_ignored():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run(
        ["git", "check-ignore", "-q", "data/books_sources.local.json"], cwd=ROOT, capture_output=True, timeout=30
    )
    assert r.returncode == 0


def test_build_pages_reads_the_local_sources_map(tmp_path):
    f = tmp_path / "sources.json"
    absolute = tmp_path / "abs.pdf"
    f.write_text(
        json.dumps({"book-a": "a/book-a.pdf", "book-b": str(absolute), "book-c": "c.epub", "book-d": 3}),
        encoding="utf-8",
    )
    assert build_pages.load_sources(f, tmp_path / "pdfs") == {
        "book-a": tmp_path / "pdfs" / "a" / "book-a.pdf",
        "book-b": absolute,
    }
