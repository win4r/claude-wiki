"""Offline end to end: wiki ask, wiki eval and wiki dream on the fixture wiki; eval scoring; the question files."""

import json
from pathlib import Path

import pytest

from wiki import WikiError, gitops, tool_root
from wiki.ask import cited_units, refused
from wiki.check import errors, run_checks
from wiki.cli import main
from wiki.dream import collect_transcripts, load_dreams
from wiki.evaluate import load_questions, parse_grade, score
from wiki.ledger import Ledger
from wiki.manifest import Manifest
from wiki.phases import build

QUESTIONS = """
[[question]]
id = "q1"
kind = "lookup"
question = "What price per share did Acme agree to?"
criteria = ["Gives the price with a source tag"]

[[question]]
id = "q2"
kind = "trap"
question = "What was Acme's revenue for fiscal year 2025?"
criteria = ["Refuses: says it is not in the wiki or documents"]
"""


@pytest.fixture
def built(loaded):
    assert build(loaded) == 0
    return loaded


def test_answer_helpers():
    assert refused("Not in the wiki. The FY2025 10-K would answer it.")
    assert refused("**Not in the documents**")
    assert not refused("The price was $44.00 [source: a | as-of: 2024-01-01]")
    units, bad = cited_units("x [source: a-1 | as-of: 2024-01-01] y [source: b | as of 2024]")
    assert "a-1" in units and bad


@pytest.mark.parametrize("mode", ["wiki", "sources", "both"])
def test_ask_each_mode(built, mode, capsys):
    assert main(["-w", str(built.root), "ask", "What price per share did Acme agree to?", "--mode", mode]) == 0
    out = capsys.readouterr().out
    assert "[source:" in out and "all in the manifest" in out
    rec = json.loads(sorted((built.runs / "ask").glob(f"*-{mode}.json"))[-1].read_text())
    assert rec["mode"] == mode and not rec["unknown_units"]
    argv = json.loads(sorted((built.runs / "ask").glob(f"*-{mode}.cmd.json"))[-1].read_text())
    cwd = Path(argv["cwd"])
    assert cwd == (built.docs if mode == "sources" else built.wiki)
    assert ("--add-dir" in argv["argv"]) == (mode == "both")
    assert not any(str(built.eval_dir) in a for a in argv["argv"])  # eval files never reach an ask run
    line = Ledger(built.ledger_path).entries()[-1]
    assert line["command"] == "ask" and line["phase"] == f"ask-{mode}"


def test_ask_retries_once_when_citing_non_units(built, capsys, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "slugcite:ask")
    assert main(["-w", str(built.root), "ask", "What price per share did Acme agree to?", "--mode", "wiki"]) == 0
    assert "all in the manifest" in capsys.readouterr().out
    rec = json.loads(sorted((built.runs / "ask").glob("*-wiki.json"))[-1].read_text())
    assert not rec["unknown_units"] and "_schema.md" in rec["citation_retry"]
    retry = json.loads(sorted((built.runs / "ask").glob("*-wiki-cite.cmd.json"))[-1].read_text())
    note = " ".join(retry["argv"])
    assert "not source units" in note and "is an entity page; its facts come from the units" in note
    assert "is a wiki file" in note
    lines = [e for e in Ledger(built.ledger_path).entries() if e["command"] == "ask"]
    assert len(lines) == 2 and rec["cost_usd"] == pytest.approx(sum(e["cost_usd"] for e in lines[-2:]))


def test_ask_trap_is_refused(built, capsys):
    assert main(["-w", str(built.root), "ask", "What was revenue in fiscal 2025?", "--mode", "wiki"]) == 0
    assert "Not in the wiki" in capsys.readouterr().out


def test_ask_estimate_and_cap(built, capsys):
    assert main(["-w", str(built.root), "ask", "q?", "--estimate"]) == 0
    assert "Estimate for one question" in capsys.readouterr().out
    assert main(["-w", str(built.root), "ask", "q?", "--max-usd", "0.001"]) == 3


def test_question_files(tmp_path):
    p = tmp_path / "q.toml"
    p.write_text(QUESTIONS)
    assert [q["id"] for q in load_questions(p)] == ["q1", "q2"]
    p.write_text(QUESTIONS.replace('kind = "trap"', 'kind = "riddle"'))
    with pytest.raises(WikiError):
        load_questions(p)
    core = load_questions(tool_root() / "examples" / "sec-deal" / "core-tier.toml")
    assert len(core) == 10 and sum(q["kind"] == "trap" for q in core) == 1
    qs_path = tool_root() / "examples" / "sec-deal" / "quickstart-tier.toml"
    if qs_path.exists():
        qs = load_questions(qs_path)
        kinds = [q["kind"] for q in qs]
        assert len(qs) == 8 and kinds.count("lookup") == 5 and kinds.count("cross-document") == 2 and kinds.count("trap") == 1


def test_grade_parsing_and_scoring():
    assert parse_grade('{"met": [true, false]}', 2) == [True, False]
    assert parse_grade('noise {"met": [true]} tail', 1) == [True]
    assert parse_grade('{"met": [true]}', 2) is None and parse_grade("nope", 1) is None
    # a grader that corrects itself: the last valid object wins
    corrected = ('{"met": [true, false], "notes": ["a", "b"]}\n\nCorrection: the second entry is wrong.\n\n'
                 '{"met": [true, true], "notes": ["a", "{b}"]}')
    assert parse_grade(corrected, 2) == [True, True]
    assert parse_grade('{"met":[false,true]}\n\nWait, need notes.\n\n{"met":[false,true],"notes":["x","y"]}', 2) == [False, True]
    assert parse_grade('{"met": [true, true]} then {"met": [true]}', 2) == [True, True]
    recs = [
        {"mode": "wiki", "kind": "lookup", "criteria": ["a", "b"], "met": [True, True], "cost_usd": 0.01,
         "refused": False, "unknown_units": []},
        {"mode": "wiki", "kind": "trap", "criteria": ["r"], "met": [True], "cost_usd": 0.03, "refused": True,
         "unknown_units": []},
        {"mode": "sources", "kind": "lookup", "criteria": ["a", "b"], "met": [True, False], "cost_usd": 0.11,
         "refused": False, "unknown_units": ["zz"]},
        {"mode": "sources", "kind": "trap", "criteria": ["r"], "met": None, "cost_usd": 0.09, "refused": False,
         "unknown_units": []},
    ]
    s = score(recs, build_cost=1.6)
    w, src = s["modes"]["wiki"], s["modes"]["sources"]
    assert w["score"] == 1.0 and src["score"] == pytest.approx(1 / 3)
    assert w["median_cost_usd"] == pytest.approx(0.02) and w["traps_refused"] == 1 and src["traps_refused"] == 0
    assert w["saving_per_answer_usd"] == pytest.approx(0.08) and w["break_even_answers"] == pytest.approx(20)
    assert src["unknown_citations"] == ["zz"] and src["ungraded"] == 1


def test_eval_end_to_end(built, tmp_path, capsys):
    qf = tmp_path / "fixture-tier.toml"
    qf.write_text(QUESTIONS)
    assert main(["-w", str(built.root), "eval", str(qf)]) == 0
    reports = sorted(built.eval_dir.glob("report-fixture-tier-*.json"))
    data = json.loads(reports[-1].read_text())
    modes = data["summary"]["modes"]
    assert set(modes) == {"wiki", "sources", "both"}
    for m in modes.values():
        assert m["traps_refused"] == 1 and m["answers"] == 2 and not m["unknown_citations"]
    assert modes["wiki"]["score"] == 1.0
    assert (built.eval_dir / reports[-1].name.replace(".json", ".md")).exists()
    # eval transcripts never feed dreaming
    assert not list((built.runs / "ask").glob("*.json")) if (built.runs / "ask").exists() else True
    graders = [e for e in Ledger(built.ledger_path).entries() if e["phase"] == "grade"]
    assert len(graders) == 6 and all(e["model"] == "sonnet" for e in graders)


def ask_n(ws, n):
    for i in range(n):
        assert main(["-w", str(ws.root), "ask", f"What price did Acme agree to, try {i}?", "--mode", "wiki"]) == 0


def test_dream_needs_five_transcripts(built, capsys):
    ask_n(built, 4)
    assert main(["-w", str(built.root), "dream"]) == 0
    assert "Fewer than 5" in capsys.readouterr().out
    assert not gitops.branches(built.repo, "dream/*")


def test_dream_accept(built, capsys):
    ask_n(built, 5)
    base = gitops.head(built.repo)
    assert main(["-w", str(built.root), "dream"]) == 0
    out = capsys.readouterr().out
    assert "passed" in out
    d = load_dreams(built)[-1]
    assert d["status"] == "review" and gitops.branches(built.repo, "dream/*") == [d["branch"]]
    assert gitops.head(built.repo) == base, "a dream never touches main by itself"
    s = d["summary"]
    assert s["findings"] == 1 and s["lines_retracted"] == 1 and s["check_errors"] == 0
    changed = gitops.diff_names(built.repo, base, d["branch"])
    assert sorted(s["files_changed"]) == sorted(changed)
    assert any(p.startswith("wiki/addenda/") for p in changed)
    assert main(["-w", str(built.root), "status"]) == 0
    assert d["branch"] in capsys.readouterr().out
    assert main(["-w", str(built.root), "dream"]) != 0  # one open dream at a time
    assert main(["-w", str(built.root), "dream", "--accept"]) == 0
    assert gitops.tags(built.repo, "dream-accepted-*") == [f"dream-accepted-{d['ts']}"]
    assert not gitops.branches(built.repo, "dream/*")
    findings = run_checks(built.wiki, Manifest.load(built.manifest_path), repo=built.repo, against=base)
    assert not errors(findings), [str(f) for f in errors(findings)]
    assert "retracted: 2024-12-31" in "".join(p.read_text() for p in (built.wiki / "notes").glob("*.md"))
    assert collect_transcripts(built) == [], "transcripts before the accepted tag are used up"


def test_dream_discard(built):
    ask_n(built, 5)
    assert main(["-w", str(built.root), "dream"]) == 0
    assert main(["-w", str(built.root), "dream", "--discard"]) == 0
    assert not gitops.branches(built.repo, "dream/*")
    assert load_dreams(built)[-1]["status"] == "discarded"
    assert len(collect_transcripts(built)) == 5, "a discarded dream leaves its transcripts for the next"


def test_dream_that_breaks_a_fact_line_is_refused(built, monkeypatch):
    ask_n(built, 5)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "lifecycle:dream_apply")
    assert main(["-w", str(built.root), "dream"]) == 2
    d = load_dreams(built)[-1]
    assert d["status"] == "failed-check"
    assert main(["-w", str(built.root), "dream", "--accept"]) != 0
    assert main(["-w", str(built.root), "dream", "--discard"]) == 0
