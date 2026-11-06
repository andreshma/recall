"""Chunking: tamanos, secciones, solapamiento, lineas y archivos omitidos."""
from recall.chunking import chunk_file, chunk_markdown, parse_title, should_skip

from conftest import DDD, para


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
    # el pie de figura (######) no abre seccion: queda dentro de "Bounded Contexts"
    assert not any(s.startswith("Figure") for s in secs)
    assert all(c.book == "Learning DDD (Khononov)" and c.chapter.startswith("Chapter 3") for c in cs)


def test_overlap_within_section():
    cs = [c for c in chunk_markdown(_ddd(), "x") if c.section == "Bounded Contexts"]
    assert len(cs) >= 2
    tail = cs[0].text[-60:]
    assert tail.split(" ", 1)[1][:30] in cs[1].text[:200]


def test_no_overlap_across_sections():
    cs = chunk_markdown(_ddd(), "x")
    for a, b in zip(cs, cs[1:]):
        if a.section != b.section:
            assert not b.text.startswith(a.text[-40:])


def test_line_numbers_point_to_source():
    text = _ddd()
    lines = text.splitlines()
    for c in chunk_markdown(text, "x"):
        assert 1 <= c.line_start <= c.line_end <= len(lines)
        src = " ".join(lines[c.line_start - 1 : c.line_end])
        # la primera palabra no solapada del chunk aparece en su rango de lineas
        assert c.text.split()[-1].strip(".") in src


def test_source_line_dropped():
    assert not any("Source:" in c.text for c in chunk_markdown(_ddd(), "x"))


def test_skip_rules_are_anchored():
    for skip in ("00-front-matter.md", "01-cover.md", "99-index.md", "03-acknowledgments.md",
                 "02-table-of-contents.md", "04-copyright-page.md", "x-index.md", "05-list-of-figures.md"):
        assert should_skip(skip), skip
    # el prototipo omitia estos por subcadena ("cover" en "recovery", "index"...)
    for keep in ("13-chapter-7-disaster-recovery.md", "20-chapter-13-service-discovery.md",
                 "02-chapter-1-anatomy-of-an-index.md", "08-preface-developers-need-to-index.md"):
        assert not should_skip(keep), keep


def test_long_paragraph_split(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# B — C\n\n# S\n\n" + ("word " * 1200), encoding="utf-8")
    cs = chunk_file(str(p), "b")
    assert len(cs) >= 5 and all(len(c.text) <= 1000 for c in cs)


def test_language_detected():
    es = chunk_markdown("# L — C\n\n# S\n\n" + para("La estimación del proyecto es más precisa con el avance y los datos.", 600), "x")
    en = chunk_markdown("# L — C\n\n# S\n\n" + para("The estimate of the project is more precise as it moves forward.", 600), "x")
    assert {c.lang for c in es} == {"es"} and {c.lang for c in en} == {"en"}
