"""Every wiki check rule, on a small hand-written wiki."""

import json

import pytest

from wiki import gitops
from wiki.check import errors, run_checks
from wiki.manifest import Manifest
from wiki.workspace import sha256_bytes

SCHEMA = "---\nname: _schema\ndescription: rules\n---\nrules\n"


def page(name, body="", **extra):
    fm = {"name": name, "description": "d", **extra}
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\n" + body


def good_wiki():
    return {
        "_schema.md": SCHEMA,
        "notes/u1.md": page("u1", "## acme\ntype: company\naliases: [\"Acme\"]\n- Fact. [source: u1 | as-of: 2024-01-01]\n"
                                  "## red-flags\n- Flag. [source: u1 | as-of: 2024-01-01]\n"),
        "open_questions/u1.md": page("u1", "- Is there a gap? -> [[nowhere]]\n"),
        "relations/u1.md": page("u1", "- [[acme]] | owns | [[acme]] [source: u1 | as-of: 2024-01-01]\n"),
        "contracts/u1.md": page("u1", "### Section 1\n> Text.\n[source: u1 | as-of: 2024-01-01 | section: 1]\n"),
        "_aliases.md": page("_aliases", "| slug | type | names | proposed | description |\n| --- | --- | --- | --- | --- |\n"
                                        "| acme | company | [\"Acme\"] | acme | The company |\n"),
        "entities/acme.md": page("acme", "<!-- current-picture:start -->\n- Fact. [source: u1 | as-of: 2024-01-01]\n"
                                         "<!-- current-picture:end -->\n## From u1\n- Fact. [source: u1 | as-of: 2024-01-01]\n",
                                 aliases='["Acme"]'),
        "_index.md": page("_index", "## Where to start\n- [[acme]]\n## Units\n- u1: notes/u1.md\n"),
        "README.md": page("README", "Start at [[_index]].\n"),
    }


def manifest():
    m = Manifest.new(sha256_bytes(SCHEMA.encode()))
    m.units["u1"] = {"document": "u1", "part": None, "parts": 1, "sha256": "x", "size": 100_000, "status": "filed"}
    return m


def write(tmp_path, files):
    w = tmp_path / "wiki"
    for path, text in files.items():
        p = w / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return w


def rules(findings, severity="error"):
    return sorted({f.rule for f in findings if f.severity == severity})


def test_good_wiki_passes(tmp_path):
    f = run_checks(write(tmp_path, good_wiki()), manifest())
    assert not errors(f), [str(x) for x in f]
    assert not [x for x in f if x.severity == "warning"], [str(x) for x in f]


@pytest.mark.parametrize("mutate,rule", [
    (lambda w: w.update({"README.md": "no frontmatter\n"}), "frontmatter"),
    (lambda w: w.update({"README.md": page("readme")}), "frontmatter"),
    (lambda w: w.update({"notes/u1.md": w["notes/u1.md"].replace(" [source: u1 | as-of: 2024-01-01]\n## red", "\n## red")}), "tag"),
    (lambda w: w.update({"notes/u1.md": w["notes/u1.md"].replace("as-of: 2024-01-01]\n## red", "as of: 2024]\n## red")}), "tag"),
    (lambda w: w.update({"metrics/u1.md": page("u1", "- EBITDA | $1 | basis | FY [source: nope | as-of: 2024-01-01]\n")}), "tag"),
    (lambda w: w.update({"entities/acme.md": w["entities/acme.md"].replace("- Fact. [source: u1 | as-of: 2024-01-01]\n<!--", "- Fact.\n<!--")}), "tag"),
    (lambda w: w.update({"notes/u9.md": page("u9")}), "unit"),
    (lambda w: w.update({"notes/u1.md": w["notes/u1.md"] + "## other\ntype: company\n- More. [source: u1 | as-of: 2024-01-01]\n"}), "aliases"),
    (lambda w: w.update({"_aliases.md": w["_aliases.md"] + "| acme2 | company | [] | acme | dup |\n"}), "aliases"),
    (lambda w: w.pop("entities/acme.md"), "pages"),
    (lambda w: w.update({"entities/ghost.md": page("ghost", aliases="[]"), "_index.md": w["_index.md"] + "- [[ghost]]\n"}), "pages"),
    (lambda w: w.update({"_index.md": page("_index", "## Units\n- u1: notes/u1.md\n")}), "index"),
    (lambda w: w.update({"_index.md": w["_index.md"].replace("- u1: notes/u1.md\n", "")}), "index"),
    (lambda w: w.update({"_index.md": w["_index.md"] + "- [[missing-page]]\n"}), "index"),
    (lambda w: w.update({"_index.md": w["_index.md"] + "- notes/u7.md\n"}), "index"),
    (lambda w: w.update({"README.md": page("README", "x" * 100_001)}), "size"),
    (lambda w: w.update({"_schema.md": SCHEMA + "edited\n"}), "schema"),
])
def test_each_error_rule(tmp_path, mutate, rule):
    files = good_wiki()
    mutate(files)
    f = run_checks(write(tmp_path, files), manifest())
    assert rule in rules(f), [str(x) for x in f]


def test_warnings_and_notes(tmp_path):
    files = good_wiki()
    files["README.md"] = page("README", "See [[nobody]].\n" + "y" * 21_000)
    files["open_questions/u1.md"] = page("u1", "- gap -> [[redlink-ok]]\n")
    files["_aliases.md"] += "| other | company | [\"acme\"] | |  x |\n"
    f = run_checks(write(tmp_path, files), manifest(), corpus_bytes=100)
    warnings = [x for x in f if x.severity == "warning"]
    assert any("[[nobody]]" in x.message for x in warnings)
    assert not any("redlink-ok" in x.message for x in warnings)  # open_questions are exempt
    assert any(x.rule == "aliases" and "claimed by" in x.message for x in warnings)
    assert any("larger than its corpus" in x.message for x in warnings)
    assert any(x.severity == "note" and x.rule == "size" for x in f)


def test_against_allows_only_lifecycle_additions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.init_repo(repo)
    w = write(repo, good_wiki())
    gitops.commit_all(repo, "base")
    base = gitops.head(repo)
    notes = w / "notes" / "u1.md"
    original = notes.read_text()
    notes.write_text(original.replace("- Fact. [source: u1 | as-of: 2024-01-01]",
                                      "- Fact. [source: u1 | as-of: 2024-01-01 | retracted: 2024-06-01]"))
    assert not [x for x in run_checks(w, manifest(), repo=repo, against=base) if x.rule == "against"]
    notes.write_text(original.replace("- Fact. [source", "- Fact rewritten. [source"))
    assert [x for x in run_checks(w, manifest(), repo=repo, against=base) if x.rule == "against"]
    notes.write_text(original.replace("- Flag. [source: u1 | as-of: 2024-01-01]\n", ""))
    assert [x for x in run_checks(w, manifest(), repo=repo, against=base) if x.rule == "against"]
    notes.write_text(original)
    (w / "relations" / "u1.md").unlink()
    assert [x for x in run_checks(w, manifest(), repo=repo, against=base) if x.rule == "against"]


def test_against_unknown_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.init_repo(repo)
    w = write(repo, good_wiki())
    gitops.commit_all(repo, "base")
    assert "against" in rules(run_checks(w, manifest(), repo=repo, against="nope"))


def test_entity_aliases_must_be_json(tmp_path):
    files = good_wiki()
    files["entities/acme.md"] = files["entities/acme.md"].replace('aliases: ["Acme"]', "aliases: Acme, Inc.")
    assert "frontmatter" in rules(run_checks(write(tmp_path, files), manifest()))
    json.loads('["Acme"]')


def test_compile_splits_large_pages_and_drops_empty_slugs(tmp_path):
    from wiki.compile import compile_pages

    files = good_wiki()
    big = "".join(f"- Fact number {i} {'x' * 150}. [source: u1 | as-of: 2024-01-01]\n" for i in range(700))
    files["notes/u1.md"] = page("u1", "## acme\ntype: company\naliases: [\"Acme\"]\n" + big)
    files["_aliases.md"] += "| gone | company | [] |  | no facts left |\n"
    files["entities/gone.md"] = page("gone", aliases="[]")
    w = write(tmp_path, files)
    res = compile_pages(w, {})
    main = (w / "entities" / "acme.md").read_text()
    assert len(main.encode()) < 5_000 and "| u1 | notes/u1.md | `## acme` | 700 |" in main  # pointers, not copies
    assert not list((w / "entities").glob("acme-facts-*.md"))
    assert not (w / "entities" / "gone.md").exists() and "gone" in res.removed
    assert "acme" in res.needs_picture
    f = run_checks(w, manifest())
    assert not [x for x in errors(f) if x.rule in ("pages", "tag", "frontmatter")], [str(x) for x in errors(f)]


def test_escalations_split_keeps_every_entry_and_passes_check(tmp_path):
    import re

    from wiki.compile import split_escalations

    files = good_wiki()
    entries = "".join(f"### {i}. Flag {i} — [[acme]]\nWhy it matters: {'z' * 600}\n"
                      f"- Flag {i}. [source: u1 | as-of: 2024-01-01]\n\n" for i in range(1, 301))
    digest = "".join(f"{i}. Flag {i} — [[acme]]\n" for i in range(1, 301))
    files["escalations.md"] = page("escalations", "## Digest\n" + digest + "\n## Entries\n" + entries)
    w = write(tmp_path, files)
    assert len((w / "escalations.md").read_bytes()) > 100_000
    written = split_escalations(w)
    pages = sorted(w.glob("escalations*.md"))
    assert len(pages) >= 3 and all(len(p.read_bytes()) < 100_000 for p in pages)
    text = "".join((w / n).read_text() for n in ["escalations.md"] + [f"escalations-{k}.md" for k in range(2, len(pages) + 1)])
    assert re.findall(r"^### (\d+)\.", text, re.M) == [str(i) for i in range(1, 301)]
    assert "## Digest" in (w / "escalations.md").read_text() and "[[escalations-2]]" in (w / "escalations.md").read_text()
    assert not [x for x in errors(run_checks(w, manifest())) if x.rule in ("size", "frontmatter", "layout")]
    assert split_escalations(w) and len(sorted(w.glob("escalations*.md"))) == len(pages)  # stable on a rerun
    assert "escalations.md" in written


ALIASED = page("_aliases", "| slug | type | names | proposed | description |\n| --- | --- | --- | --- | --- |\n"
                           "| acme | company | [\"Acme\"] | acme, acme-inc, u1#acme-co | The company |\n")


def test_links_resolve_through_aliases_and_pictures_hold_list_items(tmp_path):
    files = good_wiki()
    files["_aliases.md"] = ALIASED
    files["relations/u1.md"] = page("u1", "- [[acme-inc]] | owns | [[acme-co]] [source: u1 | as-of: 2024-01-01]\n"
                                          "- [[acme]] | sued | [[nobody]] [source: u1 | as-of: 2024-01-01]\n")
    files["entities/acme.md"] = files["entities/acme.md"].replace(
        "<!-- current-picture:end -->", "Sections: From u1.\n<!-- current-picture:end -->")
    f = run_checks(write(tmp_path, files), manifest())
    warnings = [str(x) for x in f if x.severity == "warning"]
    assert any("[[nobody]]" in w for w in warnings), warnings  # names no row: still a missing page
    assert not any("acme-inc" in w or "acme-co" in w for w in warnings), warnings
    assert [x.message for x in f if x.rule == "link" and x.severity == "note"][0].startswith("2 link(s)")
    assert [x for x in f if x.rule == "picture" and x.severity == "warning"], warnings
    assert not errors(f), [str(x) for x in errors(f)]
    # a heading two rows propose cannot be resolved
    files["_aliases.md"] = ALIASED + "| other | company | [] | acme-inc | Another |\n"
    f = run_checks(write(tmp_path / "two", files), manifest())
    assert any("[[acme-inc]]" in str(x) for x in f if x.severity == "warning")


def test_picture_over_the_line_limit_warns(tmp_path):
    files = good_wiki()
    lines = "".join(f"- Fact {i}. [source: u1 | as-of: 2024-01-01]\n" for i in range(31))
    files["entities/acme.md"] = page("acme", f"<!-- current-picture:start -->\n{lines}<!-- current-picture:end -->\n",
                                     aliases='["Acme"]')
    f = run_checks(write(tmp_path, files), manifest())
    assert any(x.rule == "picture" and "over 30" in x.message for x in f)


def test_compile_attaches_alias_relations_and_normalizes_pictures(tmp_path):
    from wiki.compile import compile_pages, normalize_pictures

    files = good_wiki()
    files["_aliases.md"] = ALIASED
    files["relations/u1.md"] = page("u1", "- [[acme-inc]] | owns | [[acme-co]] [source: u1 | as-of: 2024-01-01]\n"
                                          "- [[acme]] | sued | [[nobody]] [source: u1 | as-of: 2024-01-01]\n")
    files["entities/acme.md"] = page(
        "acme", "<!-- current-picture:start -->\n- Owned by [[acme-inc|Acme Inc]]. [source: u1 | as-of: 2024-01-01]\n"
                "Sections: From u1.\n<!-- current-picture:end -->\n## From u1\n- Fact. [source: u1 | as-of: 2024-01-01]\n",
        aliases='["Acme"]')
    w = write(tmp_path, files)
    res = compile_pages(w, {})
    text = (w / "entities" / "acme.md").read_text()
    pic = text.split("<!-- current-picture:start -->")[1].split("<!-- current-picture:end -->")[0]
    assert pic == "\n- Owned by [[acme|Acme Inc]]. [source: u1 | as-of: 2024-01-01]\n"  # list items, slug links
    assert "## From u1" not in text and "Sections:" not in text
    # the line naming [[acme-inc]] and [[acme-co]] counts once, under every name it uses
    assert "| u1 | relations/u1.md | lines naming `[[acme]]` or `[[acme-co]]` or `[[acme-inc]]` | 2 |" in text
    assert "acme" in res.needs_picture  # no known facts_sha
    f = run_checks(w, manifest())
    assert not [x for x in f if x.rule == "picture"] and not errors(f), [str(x) for x in f]
    # a picture a Compile run writes is normalized the same way
    (w / "entities" / "acme.md").write_text(text.replace("[[acme|Acme Inc]]", "[[acme-inc]]"))
    assert normalize_pictures(w, ["entities/acme.md"]) == ["entities/acme.md"]
    assert "[[acme-inc]]" not in (w / "entities" / "acme.md").read_text().split("<!-- current-picture:end -->")[0]
    assert normalize_pictures(w, ["entities/acme.md"]) == []


def test_lost_lines_allows_additions_and_lifecycle_marks_only():
    from wiki.check import lost_lines

    base = "- A. [source: u | as-of: 2024-01-01]\n- B. [source: u | as-of: 2024-01-01]\nprose\n"
    assert lost_lines(base, base + "- C. [source: u | as-of: 2024-01-01]\n") == []
    assert lost_lines(base, base.replace("A. [source: u | as-of: 2024-01-01]",
                                         "A. [source: u | as-of: 2024-01-01 | retracted: 2024-02-01]")) == []
    assert lost_lines(base, base.replace("- A.", "- A, reworded.")) == [(1, "- A. [source: u | as-of: 2024-01-01]")]
    assert lost_lines(base, "- B. [source: u | as-of: 2024-01-01]\n- A. [source: u | as-of: 2024-01-01]\n") == []
    assert len(lost_lines(base, "- A. [source: u | as-of: 2024-01-01]\n")) == 1


SCOPED = page("_aliases", "| slug | type | names | proposed | description |\n| --- | --- | --- | --- | --- |\n"
                          "| acme | company | [\"Acme\"] | u1#acme | The company |\n"
                          "| acme-holdings | company | [\"Acme Holdings\"] | u2#acme, holdco | The parent |\n")


def test_a_units_own_heading_resolves_before_another_rows_slug():
    from wiki.grammar import LinkResolver, parse_aliases, repoint_links

    rows = parse_aliases(SCOPED)[0]
    r = LinkResolver(rows, headings={("u3", "holdco"), ("u3", "acme")})
    assert r("acme", "u2") == "acme-holdings"  # u2's `## acme` is filed on acme-holdings
    assert r("acme", "u1") == "acme" and r("acme") == "acme"
    assert r("holdco", "u1") == "acme-holdings"  # a bare proposed heading
    assert r.on_page("acme", ["u2"]) == "acme"  # on a page, a slug is a slug
    assert r.on_page("holdco", ["u1", "u3"]) == "acme-holdings"
    two = parse_aliases(SCOPED.replace("| The parent |", "| The parent |\n| other | company | [] | holdco | x |"))[0]
    assert LinkResolver(two)("holdco", "u1") is None  # a heading two rows propose names no page
    # a unit's heading proposed bare by one row, though it is another row's slug, follows its section
    bare = parse_aliases(SCOPED.replace("| u2#acme, holdco |", "| acme |"))[0]
    rb = LinkResolver(bare, headings={("u3", "acme")})
    assert rb("acme", "u3") == "acme-holdings" and rb("acme", "u4") == "acme"
    # links inside code spans are quoted text, not links
    assert repoint_links("- `[[holdco]]` and [[holdco]] [source: u1 | as-of: 2024-01-01]", r.on_page) == \
        "- `[[holdco]]` and [[acme-holdings]] [source: u1 | as-of: 2024-01-01]"


def test_relations_follow_the_units_heading_onto_the_right_page(tmp_path):
    from wiki.compile import compile_pages

    files = good_wiki()
    files["_aliases.md"] = SCOPED
    files["entities/acme-holdings.md"] = page("acme-holdings", "<!-- current-picture:start -->\n"
                                              "<!-- current-picture:end -->\n", aliases='["Acme Holdings"]')
    files["notes/u2.md"] = page("u2", "## acme\ntype: company\n- The parent holds Acme. "
                                      "[source: u2 | as-of: 2024-01-01]\n")
    files["relations/u2.md"] = page("u2", "- [[acme]] | owns | [[beta]] [source: u2 | as-of: 2024-01-01]\n")
    w = write(tmp_path, files)
    compile_pages(w, {})
    holdings = (w / "entities" / "acme-holdings.md").read_text()
    assert "| u2 | relations/u2.md | lines naming `[[acme]]` | 1 |" in holdings
    assert "relations/u2.md" not in (w / "entities" / "acme.md").read_text()


def test_heading_links_warn_outside_the_per_unit_files(tmp_path):
    files = good_wiki()
    files["_aliases.md"] = ALIASED
    files["README.md"] = files["README.md"].rstrip("\n") + "\nThe target is [[acme-inc]].\n"
    files["relations/u1.md"] = page("u1", "- [[acme-inc]] | owns | [[acme]] [source: u1 | as-of: 2024-01-01]\n")
    f = run_checks(write(tmp_path, files), manifest())
    warned = [x.path for x in f if x.rule == "link" and x.severity == "warning"]
    assert warned == ["README.md"], [str(x) for x in f]  # readers cannot follow a heading link
    assert [x.message for x in f if x.rule == "link" and x.severity == "note"][0].startswith("1 link(s)")


def test_a_picture_of_exactly_30_lines_is_fine(tmp_path):
    files = good_wiki()
    lines = "".join(f"- Fact {i}. [source: u1 | as-of: 2024-01-01]\n" for i in range(30))
    files["entities/acme.md"] = page("acme", f"<!-- current-picture:start -->\n{lines}<!-- current-picture:end -->\n",
                                     aliases='["Acme"]')
    assert not [x for x in run_checks(write(tmp_path, files), manifest()) if x.rule == "picture"]


def test_normalizing_keeps_tagged_lines_and_resolves_in_the_cited_unit():
    from wiki.compile import normalize_picture
    from wiki.grammar import LinkResolver, parse_aliases

    r = LinkResolver(parse_aliases(SCOPED)[0])
    pic = ("\n- CONFLICT: cap $1M [source: u1 | as-of: 2024-01-01]\n  vs $2M [source: u2 | as-of: 2024-01-01]\n"
           "Sections: From u1.\n- Owned by [[acme]] (u2's heading). [source: u2 | as-of: 2024-01-01]\n"
           "- Parent [[holdco]]. [source: u1 | as-of: 2024-01-01]\n")
    out = normalize_picture(pic, r).splitlines()
    assert "  vs $2M [source: u2 | as-of: 2024-01-01]" in out  # kept, for wiki check to report
    assert "Sections: From u1." not in out
    assert "- Owned by [[acme]] (u2's heading). [source: u2 | as-of: 2024-01-01]" in out  # a slug stays
    assert "- Parent [[acme-holdings]]. [source: u1 | as-of: 2024-01-01]" in out


def test_lost_lines_pairs_each_line_once_whatever_the_order():
    from wiki.check import lost_lines

    x = "- S. [source: u | as-of: 2024-01-01]"
    y = "- S. [source: u | as-of: 2024-01-01 | valid_to: 2024-06-30]"
    xr = "- S. [source: u | as-of: 2024-01-01 | retracted: 2024-07-01]"
    yr = "- S. [source: u | as-of: 2024-01-01 | valid_to: 2024-06-30 | retracted: 2024-07-01]"
    assert lost_lines(f"{x}\n{y}\n", f"{y}\n{xr}\n") == []  # moved above its twin
    assert lost_lines(f"{x}\n{y}\n", f"{yr}\n{xr}\n") == []
    assert lost_lines(f"{x}\n## b\n{x}\n", f"{x}\n## b\n") == [(3, x)]  # one of two identical lines deleted
    assert lost_lines(f"{x}\n{xr}\n", f"{xr}\n") == [(1, x)]  # the unchanged line is still there; x is the lost one
    assert lost_lines(f"{y}\n", f"{x}\n") == [(1, y)]  # valid_to removed
    c = "- CONFLICT: cap $1M [source: u | as-of: 2024-01-01 | retracted: 2024-02-01] vs $2M [source: u | as-of: 2024-01-01]"
    assert lost_lines(c, c.replace(" | retracted: 2024-02-01", "")) == [(1, c)]  # the first tag counts too
    assert lost_lines(c, c.replace("$2M [source: u | as-of: 2024-01-01]",
                                   "$2M [source: u | as-of: 2024-01-01 | retracted: 2024-03-01]")) == []
    t = "- T. [source: u | as-of: 2024-01-01]"
    t5 = "- T. [source: u | as-of: 2024-01-01 | valid_to: 2024-05-31]"
    t5r = "- T. [source: u | as-of: 2024-01-01 | valid_to: 2024-05-31 | retracted: 2024-07-01]"
    t6 = "- T. [source: u | as-of: 2024-01-01 | valid_to: 2024-06-30]"
    t7 = "- T. [source: u | as-of: 2024-01-01 | valid_to: 2024-07-31]"
    tr = "- T. [source: u | as-of: 2024-01-01 | retracted: 2024-07-01]"
    # t takes t5r first, so t5 needs a path that moves t on to another line
    assert lost_lines(f"{t}\n{t5}\n{t5}\n", f"{t5r}\n{t6}\n{tr}\n") == [(3, t5)]
    assert lost_lines(f"{t}\n{t5}\n{tr}\n", f"{t5r}\n{t6}\n{t7}\n") == [(3, tr)]


def test_lost_lines_loses_what_a_maximum_matching_leaves_unpaired():
    """On random pages, as many lines are lost as a line-by-line maximum matching leaves unpaired, and each lost
    line is a base line, reported in base order."""
    import random

    from wiki.check import lost_lines
    from wiki.grammar import lifecycle_only_added

    s = "- S. [source: u | as-of: 2024-01-01{}]"
    t = "- T. [source: v | as-of: 2024-02-01{} | section: 7]"
    marks = ["", " | valid_to: 2024-05-31", " | valid_to: 2024-06-30", " | retracted: 2024-07-01", " | retracted: 2024-08-01"]
    marks += [f" | valid_to: {v} | retracted: {r}" for v in ("2024-05-31", "2024-06-30") for r in ("2024-07-01", "2024-08-01")]
    rng = random.Random(11)
    for trial in range(1800):
        texts = [s] if trial < 1500 else [s, t]  # mostly one fact text, so the lines compete for the same lines
        base = [rng.choice(texts).format(rng.choice(marks)) for _ in range(rng.randint(0, 10))]
        cur = [rng.choice(texts).format(rng.choice(marks)) for _ in range(rng.randint(0, 10))]
        holder: dict[int, int] = {}  # current line -> the base line it holds

        def augment(i: int, seen: set[int]) -> bool:
            for j, c in enumerate(cur):
                if j not in seen and lifecycle_only_added(base[i], c):
                    seen.add(j)
                    if j not in holder or augment(holder[j], seen):
                        holder[j] = i
                        return True
            return False

        paired = sum(augment(i, set()) for i in range(len(base)))
        lost = lost_lines("\n".join(base), "\n".join(cur))
        assert len(lost) == len(base) - paired, (base, cur)
        assert [n for n, _ in lost] == sorted({n for n, _ in lost}) and all(base[n - 1] == ln for n, ln in lost)


def test_lost_lines_stays_small_and_quick_on_lines_that_differ_only_in_dates():
    import datetime
    import time
    import tracemalloc

    from wiki.check import lost_lines

    def day(i: int) -> str:
        return (datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).isoformat()

    line = "- S. [source: u | as-of: 2024-01-01{}]".format
    base = "\n".join(line(f" | valid_to: {day(i)}") for i in range(200))
    cur = "\n".join(line(f" | valid_to: {day(200 + i)}") for i in range(200))
    tracing = tracemalloc.is_tracing()  # a harness may trace already (PYTHONTRACEMALLOC): leave it tracing
    if not tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    before = tracemalloc.get_traced_memory()[0]
    try:
        assert len(lost_lines(base, cur)) == 200  # every pair is compared, and none fits
        peak = tracemalloc.get_traced_memory()[1] - before
    finally:
        if not tracing:
            tracemalloc.stop()
    assert peak < 2 * 2**20  # a byte per pair; a dict entry per pair took about 5 MB here
    hubs = [line(f" | valid_to: {day(i)}") for i in range(600)]
    start = time.perf_counter()
    assert len(lost_lines("\n".join(hubs + [line("")] * 600), "\n".join(hubs))) == 600
    assert time.perf_counter() - start < 2  # one search for 600 copies of a dropped line, not 600 searches


def test_lost_lines_holds_up_with_a_thousand_identical_lines():
    import time

    from wiki.check import lost_lines

    x = "- S. [source: u | as-of: 2024-01-01]"
    xr = "- S. [source: u | as-of: 2024-01-01 | retracted: 2024-07-01]"
    start = time.perf_counter()
    assert lost_lines("\n".join([x] * 1200), "\n".join([xr] * 1200)) == []
    assert lost_lines("\n".join([x] * 1200), "\n".join([x] * 1199 + [xr])) == []
    assert lost_lines("\n".join([x] * 1200), "\n".join([x] * 1199)) == [(1200, x)]  # the later copy is the lost one
    assert len(lost_lines("\n".join([x] * 1000), "\n".join([x] * 500))) == 500
    assert lost_lines("\n".join([x] * 600 + [xr] * 600), "\n".join([xr] * 1200)) == []
    assert time.perf_counter() - start < 5


UNIT_SCOPED = page("_aliases", "| slug | type | names | proposed | description |\n| --- | --- | --- | --- | --- |\n"
                               "| acme | company | [\"Acme\"] | acme, u1#acme-co | The company |\n"
                               "| other | company | [\"Other\"] | other, u2#acme-co | Another company |\n")


def test_a_picture_link_resolves_only_where_every_cited_unit_agrees():
    from wiki.compile import normalize_picture
    from wiki.grammar import LinkResolver, parse_aliases

    r = LinkResolver(parse_aliases(UNIT_SCOPED)[0])
    assert r.on_page("acme-co", ["u1"]) == "acme" and r.on_page("acme-co", ["u2"]) == "other"
    assert r.on_page("acme-co", ["u1", "u2"]) is None and r.on_page("acme-co", ["u1", "u3"]) is None
    one = "- Parent of [[acme-co]]. [source: u1 | as-of: 2024-01-01]"
    assert normalize_picture(one, r) == one.replace("[[acme-co]]", "[[acme]]")  # through the unit the line cites
    assert normalize_picture(one.replace("u1", "u2"), r) == one.replace("u1", "u2").replace("[[acme-co]]", "[[other]]")
    assert normalize_picture(one.replace("u1", "u3"), r) == one.replace("u1", "u3")  # u3 names no page for it
    conflict = ("- CONFLICT: [[acme-co]] cap $1M [source: u1 | as-of: 2024-01-01] vs $2M "
                "[source: u2 | as-of: 2024-01-01]")
    assert normalize_picture(conflict, r) == conflict  # its units disagree, so the link stays as written
