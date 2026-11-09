"""Chunking: sizes, sections, overlap, line numbers and skipped files."""

import itertools

import pytest
from conftest import DDD, para

from recall.chunking import chunk_file, chunk_markdown, parse_title, should_skip


def _ddd():
    return DDD.format(
        p1=para("Domain complexity grows when models are inconsistent.", 700),
        p2=para("A bounded context is a boundary for a model.", 2600),
        p3=para("Marketing and sales define the lead differently.", 300),
        p4=para("The ubiquitous language must be consistent.", 900),
    )


def test_title_parsed_into_book_and_chapter():
    assert parse_title("# Learning DDD (Khononov) — Chapter 3. X", "fb") == ("Learning DDD (Khononov)", "Chapter 3. X")
    assert parse_title("# Sin separador", "fb") == ("fb", "Sin separador")


def test_chunk_sizes_bounded():
    cs = chunk_markdown(_ddd(), "learning-ddd")
    assert cs
    assert all(len(c.text) <= 1000 for c in cs), [len(c.text) for c in cs]
    assert all(len(c.text) >= 100 for c in cs)


def test_sections_follow_headings_and_figures_are_inline():
    cs = chunk_markdown(_ddd(), "learning-ddd")
    secs = {c.section for c in cs}
    assert "Bounded Contexts" in secs and "Ubiquitous Language" in secs
    # a figure caption (######) does not open a section: it stays inside "Bounded Contexts"
    assert not any(s.startswith("Figure") for s in secs)
    assert all(c.book == "Learning DDD (Khononov)" and c.chapter.startswith("Chapter 3") for c in cs)


def test_overlap_within_section():
    cs = [c for c in chunk_markdown(_ddd(), "x") if c.section == "Bounded Contexts"]
    assert len(cs) >= 2
    tail = cs[0].text[-60:]
    assert tail.split(" ", 1)[1][:30] in cs[1].text[:200]


def test_no_overlap_across_sections():
    cs = chunk_markdown(_ddd(), "x")
    for a, b in itertools.pairwise(cs):
        if a.section != b.section:
            assert not b.text.startswith(a.text[-40:])


def test_line_numbers_point_to_source():
    text = _ddd()
    lines = text.splitlines()
    for c in chunk_markdown(text, "x"):
        assert 1 <= c.line_start <= c.line_end <= len(lines)
        src = " ".join(lines[c.line_start - 1 : c.line_end])
        # the first non-overlapping word of the chunk appears in its line range
        assert c.text.split()[-1].strip(".") in src


def test_source_line_dropped():
    assert not any("Source:" in c.text for c in chunk_markdown(_ddd(), "x"))


def test_skip_rules_are_anchored():
    for skip in (
        "00-front-matter.md",
        "01-cover.md",
        "99-index.md",
        "03-acknowledgments.md",
        "02-table-of-contents.md",
        "04-copyright-page.md",
        "x-index.md",
        "05-list-of-figures.md",
    ):
        assert should_skip(skip), skip
    # the prototype skipped these by substring ("cover" in "recovery", "index"...)
    for keep in (
        "13-chapter-7-disaster-recovery.md",
        "20-chapter-13-service-discovery.md",
        "02-chapter-1-anatomy-of-an-index.md",
        "08-preface-developers-need-to-index.md",
    ):
        assert not should_skip(keep), keep


def test_long_paragraph_split(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# B — C\n\n# S\n\n" + ("word " * 1200), encoding="utf-8")
    cs = chunk_file(str(p), "b")
    assert len(cs) >= 5 and all(len(c.text) <= 1000 for c in cs)


def test_language_detected():
    es = chunk_markdown(
        "# L — C\n\n# S\n\n" + para("La estimación del proyecto es más precisa con el avance y los datos.", 600), "x"
    )
    en = chunk_markdown(
        "# L — C\n\n# S\n\n" + para("The estimate of the project is more precise as it moves forward.", 600), "x"
    )
    assert {c.lang for c in es} == {"es"} and {c.lang for c in en} == {"en"}


def test_heading_parse_is_linear_on_long_space_runs():
    """ADV-3: the old heading regex backtracked quadratically (4,000 spaces took > 90 s).

    Run in a subprocess with a timeout so that a regression fails fast instead
    of hanging the suite.
    """
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    code = "; ".join(
        [
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from recall.chunking import chunk_markdown, parse_title",
            "line = '# a' + ' ' * 20000 + 'b'",
            "assert parse_title(line, 'fb') == ('fb', 'a' + ' ' * 20000 + 'b')",
            "body = chr(10).join(['# B - C', '', line, '', '## x' + ' ' * 20000 + '#y', '', 'text ' * 30])",
            "assert chunk_markdown(body, 'fb')",
        ]
    )
    try:
        r = subprocess.run([sys.executable, "-I", "-c", code, str(src)], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("heading parsing is not linear: 20,000 spaces took more than 15 s")
    assert r.returncode == 0, r.stderr


def test_closing_hashes_need_whitespace_before_them():
    """F-11: "C#" / "F#" keep their hash; a real closing sequence is still removed."""
    from recall.chunking import parse_heading

    assert parse_title("# Programming C# — Chapter 1. Intro to F#", "fb") == (
        "Programming C#",
        "Chapter 1. Intro to F#",
    )
    assert parse_heading("## PostgreSQL in C#") == (2, "PostgreSQL in C#")
    assert parse_heading("## Closed heading ##") == (2, "Closed heading")
    assert parse_heading("### Tabbed\t#") == (3, "Tabbed")
    assert parse_heading("#NoSpace") is None
    assert parse_heading("####### seven") is None
    assert parse_heading("#   ") is None
    cs = chunk_markdown("# B — C\n\n## Interop with C#\n\n" + para("Calling native code from managed code.", 400), "x")
    assert {c.section for c in cs} == {"Interop with C#"}


def test_short_last_section_is_attached_not_dropped():
    """F-11: a short final section (< 100 chars) used to vanish from the index."""
    text = (
        "# B — C\n\n# Main\n\n"
        + para("Long body about bounded contexts.", 500)
        + "\n\n# Summary\n\nKeep models small.\n"
    )
    cs = chunk_markdown(text, "x")
    assert any("Keep models small." in c.text for c in cs)
    last = cs[-1]
    assert last.line_end == len(text.splitlines()) and len(last.text) <= 1000


def test_short_paragraph_before_a_long_one_is_kept():
    """A short intro followed by a paragraph that fills a whole chunk was dropped too."""
    big = ("Event sourcing stores every change as an event. " * 25)[:990].strip()
    text = f"# B — C\n\n# A\n\nShort intro line.\n\n{big}\n"
    cs = chunk_markdown(text, "x")
    assert any("Short intro line." in c.text for c in cs)
    assert all(len(c.text) <= 1000 for c in cs)


def test_file_with_almost_no_text_yields_no_chunks():
    """Part dividers and title pages ("Part II ... intentionally left blank") are not indexed."""
    assert chunk_markdown("# B — Part II\n\n# Part II\n\n_This page intentionally left blank_\n", "x") == []


def test_skip_rules_do_not_swallow_chapters_named_contents_or_copyright():
    """F-11: "contents-.*" / "copyright.*" were not exact matches, as USAGE §4 promises."""
    for keep in (
        "03-contents-of-a-container-image.md",
        "12-copyright-law-for-developers.md",
        "07-copyrighted-material-in-training-data.md",
    ):
        assert not should_skip(keep), keep
    for skip in (
        "02-contents.md",
        "05-contents-foreword-by-martin-fowler-07-foreword-1-xhtml.md",
        "04-copyright-2023-manning-publications.md",
        "03-copyright-and-credits.md",
        "04-table-of-contents-the-real-thing.md",
        "73-microservices-patterns-back-cover.md",
    ):
        assert should_skip(skip), skip
