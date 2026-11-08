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
    assert len(main.encode()) < 80_000 and "[[acme-facts-1]]" in main
    facts = sorted((w / "entities").glob("acme-facts-*.md"))
    assert len(facts) >= 2 and all(len(p.read_bytes()) <= 80_000 for p in facts)
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
