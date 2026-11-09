"""Grammars, conversion, slugs and units, splitting, batching and manifest diffs."""

import json
import os

import pytest

from wiki import WikiError
from wiki.convert import convert, with_source_line
from wiki.grammar import (
    fact_tag, is_slug, lifecycle_only_added, parse_aliases, parse_sections, parse_tag, render_aliases,
    resolve_heading, slugify, strip_lifecycle, tags_in,
)
from wiki.manifest import Manifest, doc_slug, make_batches, split_units


# --- slugs and tags

def test_slug_rules():
    assert slugify("Squarespace, Inc.") == "squarespace-inc"
    assert doc_slug("8-k_20240909.txt") == "8-k-20240909"
    assert doc_slug("10k_fy2023.part1.txt") == "10k-fy2023-part1"
    assert is_slug("a-b-1") and not is_slug("a_b") and not is_slug("A") and not is_slug("a--b") and not is_slug("-a")


def test_tag_grammar_order_and_fields():
    assert parse_tag("[source: u-1 | as-of: 2024-02-28]") == {
        "unit": "u-1", "as_of": "2024-02-28", "valid_from": None, "valid_to": None, "retracted": None, "section": None}
    full = "[source: u | as-of: 2024-01-01 | valid_from: 2024-01-02 | valid_to: 2024-01-03 | retracted: 2024-01-04 | section: 2.1]"
    assert parse_tag(full)["section"] == "2.1"
    # wrong order, unknown field, bad date, missing as-of
    for bad in ("[source: u | valid_to: 2024-01-03 | as-of: 2024-01-01]",
                "[source: u | as-of: 2024-01-01 | note: x]",
                "[source: u | as-of: 2024-1-1]",
                "[source: u]",
                "[source: U_1 | as-of: 2024-01-01]"):
        assert parse_tag(bad) is None, bad


def test_fact_line_and_inline_tags():
    line = "- Price $46.50. [source: a | as-of: 2024-01-01]"
    assert fact_tag(line)["unit"] == "a"
    assert fact_tag("- Price $46.50.") is None
    good, bad = tags_in("- CONFLICT: $A [source: a | as-of: 2024-01-01] vs $B [source: b | as-of: 2024-01-02]")
    assert [t["unit"] for t in good] == ["a", "b"] and not bad
    good, bad = tags_in("- x [source: a | as of 2024]")
    assert not good and bad


def test_lifecycle_comparison():
    base = "- Fact. [source: a | as-of: 2024-01-01]"
    assert lifecycle_only_added(base, base)
    assert lifecycle_only_added(base, "- Fact. [source: a | as-of: 2024-01-01 | valid_to: 2024-05-01]")
    assert lifecycle_only_added(base, "- Fact. [source: a | as-of: 2024-01-01 | retracted: 2024-05-01]")
    assert not lifecycle_only_added(base, "- Fact changed. [source: a | as-of: 2024-01-01]")
    assert not lifecycle_only_added(base, "- Fact. [source: b | as-of: 2024-01-01]")
    with_vt = "- Fact. [source: a | as-of: 2024-01-01 | valid_to: 2024-05-01]"
    assert not lifecycle_only_added(with_vt, base)  # removing valid_to is a change
    assert strip_lifecycle(with_vt) == base


def test_notes_sections():
    text = """---
name: u
description: d
---
## acme
type: company
aliases: ["Acme, Inc.", "the Company"]
- Fact one. [source: u | as-of: 2024-01-01]
## red-flags
- A flag. [source: u | as-of: 2024-01-01]
## Bad_Heading
aliases: not json
"""
    secs = parse_sections(text)
    assert [s.heading for s in secs] == ["acme", "red-flags", "Bad_Heading"]
    assert secs[0].type == "company" and secs[0].aliases == ["Acme, Inc.", "the Company"]
    assert len(secs[0].facts) == 1
    assert len(secs[2].problems) == 2


def test_alias_table_round_trip_and_resolution():
    text = """| slug | type | names | proposed | description |
| --- | --- | --- | --- | --- |
| acme | company | ["Acme, Inc.", "a | b"] | acme, the-company, u2#falcon | The company |
| falcon | event | [] | u1#falcon | Project |
"""
    rows, problems = parse_aliases(text)
    assert not problems
    assert rows[0].names == ["Acme, Inc.", "a | b"]
    assert resolve_heading(rows, "u2", "falcon") == ["acme"]
    assert resolve_heading(rows, "u1", "falcon") == ["falcon"]
    assert resolve_heading(rows, "u3", "the-company") == ["acme"]
    again, _ = parse_aliases(render_aliases(rows))
    assert [(r.slug, r.proposed, r.names) for r in again] == [(r.slug, r.proposed, r.names) for r in rows]
    _, problems = parse_aliases("| a | b | notjson | x | y |\n| only | three | cols |\n")
    assert len(problems) == 2


# --- conversion

def test_convert_stdlib_types(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.md").write_text("# T\nbody")
    (tmp_path / "c.html").write_text("<html><head><title>x</title></head><body><p>Para</p>"
                                     "<table><tr><td>A</td><td>B</td></tr></table><script>no()</script></body></html>")
    (tmp_path / "d.csv").write_text("a,b\n1,2\n")
    (tmp_path / "e.eml").write_text("From: a@x\nTo: b@x\nSubject: Hi\nDate: Mon, 1 Jan 2024 00:00:00 +0000\n"
                                    "Content-Type: text/plain\n\nBody text\n")
    assert convert(tmp_path / "a.txt") == "hello"
    assert "Para" in convert(tmp_path / "c.html") and "A | B" in convert(tmp_path / "c.html")
    assert "no()" not in convert(tmp_path / "c.html")
    assert "1,2" in convert(tmp_path / "d.csv")
    eml = convert(tmp_path / "e.eml")
    assert "Subject: Hi" in eml and "Body text" in eml
    (tmp_path / "f.xyz").write_text("x")
    with pytest.raises(WikiError):
        convert(tmp_path / "f.xyz")


def test_convert_office_types(tmp_path):
    docx = pytest.importorskip("docx")
    openpyxl = pytest.importorskip("openpyxl")
    pptx = pytest.importorskip("pptx")
    d = docx.Document()
    d.add_heading("Note", level=1)
    d.add_paragraph("Body para")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "H1", "H2", "v1", "v2"
    d.save(tmp_path / "n.docx")
    out = convert(tmp_path / "n.docx")
    assert "# Note" in out and "Body para" in out and "| v1 | v2 |" in out
    wb = openpyxl.Workbook()
    wb.active.title = "S"
    wb.active.append(["a", 1])
    wb.save(tmp_path / "w.xlsx")
    assert "## Sheet: S" in convert(tmp_path / "w.xlsx") and "a | 1" in convert(tmp_path / "w.xlsx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Slide title"
    prs.save(tmp_path / "p.pptx")
    assert "## Slide 1" in convert(tmp_path / "p.pptx") and "Slide title" in convert(tmp_path / "p.pptx")


def _pdf_with_text(path, text):
    # a minimal one-page PDF with a text layer, written by hand
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_convert_pdf_text_layer_and_scanned_refusal(tmp_path):
    pytest.importorskip("pypdf")
    _pdf_with_text(tmp_path / "t.pdf", "This filing has a real text layer with enough characters to pass.")
    assert "real text layer" in convert(tmp_path / "t.pdf")
    _pdf_with_text(tmp_path / "s.pdf", "")
    with pytest.raises(WikiError, match="scanned"):
        convert(tmp_path / "s.pdf")


def test_source_line():
    assert with_source_line("body", "x.txt", today="2024-01-01").startswith("[SOURCE: x.txt | added: 2024-01-01]\n")
    kept = with_source_line("[SOURCE: SEC | filed: 2024-01-01]\n\nbody", "x.txt")
    assert kept.startswith("[SOURCE: SEC | filed: 2024-01-01]")
    replaced = with_source_line("[SOURCE: old]\nbody", "x.docx", source_line="[SOURCE: new]")
    assert replaced.startswith("[SOURCE: new]\n") and "old" not in replaced


# --- units, splitting, batching, manifest

def test_split_keeps_source_line_and_parts():
    text = "[SOURCE: SEC | filed: 2024-01-01]\n\n" + "".join(f"line {i} " + "x" * 90 + "\n" for i in range(100))
    parts = split_units("doc", text, 3000)
    assert len(parts) > 1
    for i, (unit, part, n, body) in enumerate(parts, 1):
        assert unit == f"doc-part{i}" and part == i and n == len(parts)
        assert body.startswith(f"[SOURCE: SEC | filed: 2024-01-01 | part {i} of {n}]\n")
        assert len(body) <= 3000
    assert split_units("doc", "[SOURCE: s]\nshort", 3000) == [("doc", None, 1, "[SOURCE: s]\nshort")]
    joined = "".join(b.split("\n\n", 1)[1] for _, _, _, b in parts)
    assert joined == text.split("\n\n", 1)[1]


def test_batches_pack_small_units_and_isolate_large():
    sizes = {"a": 100, "b": 200, "c": 2900, "d": 50}
    b = make_batches(["a", "b", "c", "d"], sizes, 3000)
    assert b == {"b01": ["a", "b"], "b02": ["c", "d"]} or all(sum(sizes[u] for u in us) <= 3000 or len(us) == 1
                                                              for us in b.values())
    assert sum(len(v) for v in b.values()) == 4


def test_manifest_diffs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    m = Manifest.new("sha")
    text = "[SOURCE: s]\n\nshort body\n"
    assert m.add_document("a.txt", text, docs, 3000) == [("a", "new")]
    assert (docs / "a.txt").read_text() == text
    assert not os.access(docs / "a.txt", os.W_OK) or os.stat(docs / "a.txt").st_mode & 0o222 == 0
    assert m.add_document("a.txt", text, docs, 3000) == [("a", "unchanged")]
    m.units["a"]["status"] = "filed"
    assert m.add_document("a.txt", text + "more\n", docs, 3000) == [("a", "changed")]
    assert m.pending_units() == ["a"]
    long = "[SOURCE: s]\n\n" + ("y" * 99 + "\n") * 80
    res = m.add_document("a.txt", long, docs, 3000)
    assert ("a", "removed") in res and all(s == "new" for u, s in res if u != "a")
    assert m.units["a"]["status"] == "removed" and "a" not in m.live_units() and "a" in m.unit_names()
    with pytest.raises(WikiError, match="slug"):
        m.add_document("a.md", "x", docs, 3000)
    path = tmp_path / "manifest.json"
    m.save(path)
    assert json.loads(path.read_text())["units"] == Manifest.load(path).units


def test_a_manifest_save_that_fails_keeps_the_old_manifest_and_leaves_no_partial_copy(tmp_path, monkeypatch):
    from pathlib import Path

    (tmp_path / "repo").mkdir()
    path = tmp_path / "repo" / "manifest.json"
    Manifest.new("old").save(path)
    before = path.read_text()

    def full_disk(self, *args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", full_disk)
    with pytest.raises(OSError, match="No space"):
        Manifest.new("new").save(path)
    assert path.read_text() == before and [p.name for p in path.parent.iterdir()] == ["manifest.json"]
