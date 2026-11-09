"""Offline end to end: wiki ask, wiki eval and wiki dream on the fixture wiki; eval scoring; the question files."""

import json
from pathlib import Path

import pytest

from wiki import WikiError, gitops, tool_root
from wiki.ask import cited_units, refused
from wiki.check import errors, run_checks
from wiki.cli import main
from wiki.dream import collect_transcripts, load_dreams, transcript_markdown
from wiki.evaluate import load_questions, parse_grade, report_markdown, score
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
    assert rec["usage"]["input_tokens"] == 200  # both runs' tokens
    assert "_schema.md" in rec["first_answer"] and Path(rec["first_stream"]).exists()
    assert rec["first_stream"] != rec["stream"]


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
    assert w["by_kind"]["lookup"] == {"answers": 1, "criteria_met": 2, "criteria_total": 2, "score": 1.0}
    assert src["by_kind"]["trap"]["criteria_met"] == 0 and src["by_kind"]["lookup"]["score"] == 0.5
    assert w["median_tokens"] is None  # these records carry no usage
    recs[0]["usage"] = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 100, "cache_write_tokens": 0}
    s = score(recs, build_cost=1.6)
    assert s["modes"]["wiki"]["median_tokens"] == 115
    md = report_markdown("t.toml", {**s, "wiki_commit": "abcdef1234567"}, [])
    assert "| mode | lookup | trap | median tokens |" in md and "| wiki | 100% (2/2) | 100% (1/1) | 115 |" in md
    assert "wiki at commit abcdef1234" in md


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
    assert data["summary"]["wiki_commit"] and modes["wiki"]["median_tokens"] == 1350
    assert all(r["usage"]["cache_read_tokens"] == 1000 for r in data["answers"])
    assert "By question kind" in (built.eval_dir / reports[-1].name.replace(".json", ".md")).read_text()
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


def test_transcript_digest_shows_flags_calls_and_results(tmp_path):
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Grep", "input": {
            "pattern": "fee", "glob": "{notes,metrics}/*.md", "-i": True, "head_limit": 30, "output_mode": "count"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "notes/x.md:3\nmetrics/y.md:1"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b", "name": "Read", "input": {
            "file_path": "notes/x.md", "offset": 1, "limit": 40}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "content": [
            {"type": "text", "text": "\n".join(f"line {i}" for i in range(20))}]}]}},
    ]
    stream = tmp_path / "s.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    first = tmp_path / "f.jsonl"
    first.write_text(json.dumps(events[0]) + "\n")
    rec = {"command": "ask", "mode": "wiki", "time": "2026-11-09T00:00:00+00:00", "question": "What fee?",
           "answer": "Not in the wiki", "refused": True, "unknown_units": [], "citation_retry": ["acme"],
           "malformed_tags": [], "turns": 4, "outcome": "success", "stream": str(stream),
           "first_stream": str(first), "first_answer": "A fee. [source: acme | as-of: 2024-01-01]"}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rec))
    md = transcript_markdown(p)
    assert '- Grep(pattern="fee", glob="{notes,metrics}/*.md", output_mode="count", -i=true, head_limit=30)' in md
    assert "    > notes/x.md:3" in md and '- Read(file_path="notes/x.md", offset=1, limit=40)' in md
    assert "    > line 7" in md and "    > line 8" not in md and "(12 more line(s))" in md
    flags = [ln for ln in md.splitlines() if ln.startswith("flags: ")][0]
    assert "Not in" in flags and "acme" in flags
    assert "## First attempt" in md and "First answer:" in md
    assert md.index("## First attempt") < md.index("## What the reader did") < md.index("## Answer")
    rec.pop("first_stream")
    rec.update(refused=False, citation_retry=[], stream=str(tmp_path / "gone.jsonl"))
    p.write_text(json.dumps(rec))
    md = transcript_markdown(p)
    assert "flags: none" in md and "(stream not found)" in md and "## First attempt" not in md


def test_a_failed_citation_retry_is_recorded(built, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "slugfail:ask")
    main(["-w", str(built.root), "ask", "What price per share did Acme agree to?", "--mode", "wiki"])
    path = sorted((built.runs / "ask").glob("*-wiki.json"))[-1]
    rec = json.loads(path.read_text())
    assert "_schema.md" in rec["citation_retry"] and rec["unknown_units"]  # asked again; the first answer stands
    assert rec["retry_outcome"] == "error_max_budget_usd" and Path(rec["retry_stream"]).exists()
    assert "first_stream" not in rec
    assert "the retry ended with error_max_budget_usd, so the first answer was kept" in transcript_markdown(path)
    s = score([{**rec, "kind": "lookup", "criteria": ["a"], "met": [True]}], build_cost=1.0)
    assert s["modes"]["wiki"]["citation_retries"] == 1


def test_eval_refuses_uncommitted_wiki_changes_and_commits_only_its_report(built, tmp_path, capsys):
    qf = tmp_path / "fixture-tier.toml"
    qf.write_text(QUESTIONS)
    page = built.wiki / "entities" / "acme.md"
    page.write_text(page.read_text() + "\nA hand edit.\n")
    runs = len(Ledger(built.ledger_path).entries())
    assert main(["-w", str(built.root), "eval", str(qf)]) != 0
    assert "not committed" in capsys.readouterr().err
    assert len(Ledger(built.ledger_path).entries()) == runs  # nothing was spent
    gitops.git(built.repo, "checkout", "--", "wiki")
    m = Manifest.load(built.manifest_path)  # manifest.json alone counts too
    del m.units["alpha-8k"]
    m.save(built.manifest_path)
    assert main(["-w", str(built.root), "eval", str(qf)]) != 0
    assert "not committed" in capsys.readouterr().err
    assert len(Ledger(built.ledger_path).entries()) == runs
    gitops.git(built.repo, "checkout", "--", "manifest.json")
    (built.repo / "notes-to-self.txt").write_text("not part of any report\n")
    assert main(["-w", str(built.root), "eval", str(qf)]) == 0
    assert "notes-to-self.txt" in gitops.git(built.repo, "status", "--porcelain", "--untracked-files=all")  # left alone
    committed = gitops.git(built.repo, "show", "--name-only", "--format=", "HEAD").split()
    assert len(committed) == 2 and all(p.startswith("eval/report-fixture-tier-") for p in committed)


def test_dream_apply_that_rewords_a_filed_line_is_rejected_and_retried(built, monkeypatch):
    ask_n(built, 5)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "reword:dream_apply:1")
    assert main(["-w", str(built.root), "dream"]) == 0
    d = load_dreams(built)[-1]
    assert d["status"] == "review"
    vio = sorted(built.runs.glob("dream-*/apply.violations.txt"))
    assert vio and "may only gain valid_to or retracted" in vio[-1].read_text()
    assert "Rechecked" not in gitops.git(built.repo, "grep", "Rechecked", d["branch"], "--", "wiki", check=False)
    assert main(["-w", str(built.root), "dream", "--discard"]) == 0


def test_transcript_digest_marks_errors_and_keeps_events_whole(tmp_path):
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Grep", "input": {"pattern": "x", "-n": True, "-P": True}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "An unexpected parameter"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "zz", "content": "orphan"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "b", "name": "Read", "input": {"file_path": "doc.txt"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "before after"}]}},
    ]
    stream = tmp_path / "s.jsonl"
    stream.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")  # U+2028 left raw
    rec = {"mode": "wiki", "question": "q", "answer": "a", "stream": str(stream)}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rec))
    md = transcript_markdown(p).splitlines()
    i = md.index('- Grep(pattern="x", -n=true)')
    assert md[i + 1:i + 3] == ["    > (error)", "    > An unexpected parameter"]
    assert md[i + 3:i + 5] == ["- (a tool call)", "    > orphan"]
    assert md[i + 5:i + 8] == ['- Read(file_path="doc.txt")', "    > before", "    > after"]


def test_median_tokens_is_the_median():
    recs = [{"mode": "wiki", "kind": "lookup", "criteria": ["a"], "met": [True], "cost_usd": 0.01, "refused": False,
             "unknown_units": [], "usage": {"input_tokens": n}} for n in (10, 1000, 30)]
    assert score(recs, build_cost=1.0)["modes"]["wiki"]["median_tokens"] == 30


def test_an_eval_of_the_wiki_waits_for_an_unfinished_build(loaded, tmp_path, capsys, monkeypatch):
    from wiki.phases import latest_unfinished

    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "fail:compile")
    assert build(loaded) == 2
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    # the stopped phase committed its partial work, so only the unfinished build can stop the eval
    assert gitops.git(loaded.repo, "status", "--porcelain", "--untracked-files=all").strip() == "" and latest_unfinished(loaded) is not None
    qf = tmp_path / "fixture-tier.toml"
    qf.write_text(QUESTIONS)
    runs = len(Ledger(loaded.ledger_path).entries())
    capsys.readouterr()
    assert main(["-w", str(loaded.root), "eval", str(qf)]) != 0
    assert "A build is unfinished" in capsys.readouterr().err
    assert len(Ledger(loaded.ledger_path).entries()) == runs  # nothing was spent
    # a sources-only eval reads no wiki, so it may run, and its report names no wiki commit
    assert main(["-w", str(loaded.root), "eval", str(qf), "--modes", "sources"]) == 0
    assert "wiki at commit" not in capsys.readouterr().out
    report = json.loads(sorted(loaded.eval_dir.glob("report-fixture-tier-*.json"))[-1].read_text())
    assert report["summary"]["wiki_commit"] is None


def test_eval_refuses_a_mistyped_repeated_or_missing_mode_before_any_run(built, tmp_path, capsys):
    qf = tmp_path / "fixture-tier.toml"
    qf.write_text(QUESTIONS)
    runs = len(Ledger(built.ledger_path).entries())
    for modes in ("sources,Wiki", "sources,sources", ","):
        capsys.readouterr()
        assert main(["-w", str(built.root), "eval", str(qf), "--modes", modes]) != 0
        out = capsys.readouterr()
        assert "--modes takes one or more of wiki, sources, both, each once" in out.err and "Estimate" not in out.out
    from wiki.evaluate import run_eval

    for modes in (("wiki", "wiki"), ("Wiki",), ()):  # run_eval refuses on its own, whoever calls it
        with pytest.raises(WikiError, match="--modes takes"):
            run_eval(built, qf, estimate_only=True, modes=modes)
    assert "Estimate" not in capsys.readouterr().out
    assert len(Ledger(built.ledger_path).entries()) == runs


def test_eval_commits_a_question_file_kept_in_the_repo_and_refuses_one_git_ignores(built, capsys):
    qf = built.eval_dir / "tier-x.toml"
    qf.write_text(QUESTIONS)
    assert main(["-w", str(built.root), "eval", str(qf), "--modes", "wiki"]) == 0
    assert "eval/tier-x.toml" in gitops.git(built.repo, "show", "--name-only", "--format=", "HEAD").split()
    assert gitops.is_clean(built.repo)
    (built.repo / ".gitignore").write_text("eval/private-*.toml\n")
    gitops.commit_paths(built.repo, [".gitignore"], "keep private question files out of git")
    private = built.eval_dir / "private-x.toml"
    private.write_text(QUESTIONS)
    runs = len(Ledger(built.ledger_path).entries())
    capsys.readouterr()
    assert main(["-w", str(built.root), "eval", str(private), "--modes", "wiki"]) != 0
    assert "git ignores eval/private-x.toml" in capsys.readouterr().err
    assert len(Ledger(built.ledger_path).entries()) == runs  # refused before any run, not after all of them
    assert gitops.git(built.repo, "diff", "--cached", "--name-only").strip() == ""
    # a name that reads as a glob matching a tracked file is still the ignored file it names
    gitops.git(built.repo, "add", "-f", "eval/private-x.toml")
    gitops.git(built.repo, "commit", "-q", "-m", "track one private question file")
    globbed = built.eval_dir / "private-[x].toml"
    globbed.write_text(QUESTIONS)
    assert gitops.ignored(built.repo, ["eval/private-x.toml", "eval/private-[x].toml"]) == ["eval/private-[x].toml"]
    capsys.readouterr()
    assert main(["-w", str(built.root), "eval", str(globbed), "--modes", "wiki"]) != 0
    assert "git ignores eval/private-[x].toml" in capsys.readouterr().err
    assert len(Ledger(built.ledger_path).entries()) == runs


def test_eval_sees_an_untracked_wiki_file_whatever_git_status_shows(built, tmp_path, capsys):
    gitops.git(built.repo, "config", "status.showUntrackedFiles", "no")
    (built.wiki / "notes" / "stray.md").write_text("- A stray page.\n")
    qf = tmp_path / "fixture-tier.toml"
    qf.write_text(QUESTIONS)
    runs = len(Ledger(built.ledger_path).entries())
    assert main(["-w", str(built.root), "eval", str(qf), "--modes", "wiki"]) != 0
    assert "not committed" in capsys.readouterr().err
    assert len(Ledger(built.ledger_path).entries()) == runs and not gitops.is_clean(built.repo)


def test_commit_paths_takes_its_paths_literally_and_leaves_other_changes(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    gitops.init_repo(repo)
    (repo / "base.txt").write_text("x\n")
    gitops.commit_all(repo, "base")
    (repo / "eval").mkdir()
    (repo / "eval" / "q[1].toml").write_text("a\n")
    (repo / "eval" / "q1.toml").write_text("b\n")  # what the name would match as a glob
    (repo / "keep.txt").write_text("c\n")
    gitops.git(repo, "add", "keep.txt")
    assert gitops.commit_paths(repo, ["eval/q[1].toml"], "m")
    assert gitops.git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["eval/q[1].toml"]
    assert set(gitops.git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()) == {"A  keep.txt", "?? eval/q1.toml"}
    (repo / ".gitignore").write_text("*.json\n")
    (repo / "r.md").write_text("d\n")
    (repo / "r.json").write_text("{}\n")
    assert gitops.ignored(repo, ["r.md", "r.json"]) == ["r.json"]
    with pytest.raises(WikiError):
        gitops.commit_paths(repo, ["r.md", "r.json"], "m")
    assert gitops.git(repo, "diff", "--cached", "--name-only").split() == ["keep.txt"]  # r.md is unstaged again
