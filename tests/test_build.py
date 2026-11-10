"""Offline end to end: wiki build over the fixture corpus, resume, incremental builds, and every guard."""

import json
import os
import subprocess
import sys
import time

import pytest

from wiki import WikiError, gitops
from wiki.check import errors, run_checks
from wiki.cli import main
from wiki.documents import add
from wiki.ledger import Ledger
from wiki.manifest import Manifest
from wiki.phases import build, latest_unfinished


def state_of(ws):
    return json.loads(sorted(ws.runs.glob("build-*/state.json"))[-1].read_text())


def check_ok(ws):
    findings = run_checks(ws.wiki, Manifest.load(ws.manifest_path), repo=ws.repo)
    assert not errors(findings), [str(f) for f in errors(findings)]


def no_worktrees_left(ws):
    listed = gitops.git(ws.repo, "worktree", "list").strip().splitlines()
    assert len(listed) == 1, listed
    assert not list(ws.runs.glob("build-*/wt/*"))


def test_fixture_build_passes_check(loaded):
    ws = loaded
    m = Manifest.load(ws.manifest_path)
    assert [u for u in m.units if u.startswith("gamma-10k-part")], "the long document splits into parts"
    assert build(ws) == 0
    check_ok(ws)
    m = Manifest.load(ws.manifest_path)
    assert all(rec["status"] == "filed" for rec in m.units.values())
    for u in m.units:
        assert (ws.wiki / "notes" / f"{u}.md").exists()
    assert (ws.wiki / "escalations.md").exists()
    acme = (ws.wiki / "entities" / "acme.md").read_text()
    assert "<!-- current-picture:start -->\n- " in acme and "## Sources" in acme and "| alpha-8k | notes/alpha-8k.md |" in acme and "| beta-note |" in acme
    log = gitops.git(ws.repo, "log", "--format=%s").splitlines()
    for n, phase in enumerate(["convert", "batch", "extract", "check", "resolve", "link", "compile", "index"], 1):
        assert any(f"phase {n} {phase}" in s for s in log) or phase == "convert", (phase, log)
    st = state_of(ws)
    assert st["finished"] and all(t["status"] == "done" for t in st["tasks"].values())
    assert "RESOLVED:" in (ws.wiki / "open_questions" / "alpha-8k.md").read_text()
    no_worktrees_left(ws)
    # documents and transcripts never enter history
    tracked = gitops.git(ws.repo, "ls-files").splitlines()
    assert all(p.startswith(("wiki/", "eval/", "manifest.json")) for p in tracked)
    assert oct(os.stat(ws.runs).st_mode & 0o777) == "0o700"
    assert Ledger(ws.ledger_path).spent() == pytest.approx(0.01 * len(st["tasks"]))


def test_estimate_spends_nothing(loaded, capsys):
    assert main(["-w", str(loaded.root), "build", "--estimate"]) == 0
    assert "Estimate for wiki build" in capsys.readouterr().out
    assert Ledger(loaded.ledger_path).entries() == []
    assert latest_unfinished(loaded) is None


def test_over_cap_stops_and_asks(loaded, capsys, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_COST", "0.00001")
    assert main(["-w", str(loaded.root), "build", "--max-usd", "0.001"]) == 3
    assert "--approve-over-cap" in capsys.readouterr().err
    assert Ledger(loaded.ledger_path).entries() == []
    assert main(["-w", str(loaded.root), "build", "--max-usd", "0.001", "--approve-over-cap"]) == 0


def test_build_stops_at_its_cap(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_COST", "0.05")
    assert build(loaded, max_usd=0.2, approve_over_cap=True) == 2
    st = state_of(loaded)
    assert "cap" in st["stopped"]
    assert Ledger(loaded.ledger_path).spent() <= st["cap"] + 0.05 * 2  # a run in flight can pass its share


def test_hard_stop_at_total(loaded, capsys):
    text = loaded.toml.read_text().replace("total_usd = 50.0", "total_usd = 0.001")
    loaded.toml.write_text(text)
    assert main(["-w", str(loaded.root), "build"]) == 4
    assert "shortfall" in capsys.readouterr().err


def test_incremental_build(loaded, corpus):
    ws = loaded
    assert build(ws) == 0
    before = Ledger(ws.ledger_path).entries()
    assert build(ws) == 0  # nothing new: no runs
    assert Ledger(ws.ledger_path).entries() == before
    (corpus / "beta_note.md").write_text((corpus / "beta_note.md").read_text().replace(
        "ENTITY: gamma-lenders | lender | Gamma Lenders\nFACT: gamma-lenders | Gamma Lenders provide a $2.3B term loan.\n",
        "ENTITY: epsilon-bank | lender | Epsilon Bank\nFACT: epsilon-bank | Epsilon Bank provides a revolver.\n"))
    add(ws, [corpus / "beta_note.md"])
    assert Manifest.load(ws.manifest_path).pending_units() == ["beta-note"]
    assert build(ws) == 0
    check_ok(ws)
    st = state_of(ws)
    assert st["units"] == ["beta-note"]
    assert {t for t in st["tasks"] if t.startswith(("extract", "check"))} == {"extract-b01", "check-b01"}
    assert (ws.wiki / "entities" / "epsilon-bank.md").exists()
    assert not (ws.wiki / "entities" / "gamma-lenders.md").exists(), "a slug left with no facts loses its page"
    assert "addenda" in (ws.wiki / "addenda" / "beta-note.md").as_posix()  # addenda stay
    assert (ws.wiki / "addenda" / "beta-note.md").exists()


# --- guards

def test_write_outside_owned_paths_is_discarded_and_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "outside:extract:1")
    assert build(loaded) == 0
    st = state_of(loaded)
    assert any(t["attempts"] == 2 for k, t in st["tasks"].items() if k.startswith("extract"))
    assert not (loaded.wiki / "entities" / "intruder.md").exists()
    assert list(loaded.runs.glob("build-*/*.violations.txt"))
    retry = json.loads(next(loaded.runs.glob("build-*/extract-*-retry.cmd.json")).read_text())["argv"]
    task = retry[retry.index("-p") + 1]
    assert "previous attempt was rejected" in task and "entities/intruder.md" in task
    first = json.loads(next(p for p in sorted(loaded.runs.glob("build-*/extract-b*.cmd.json"))
                            if "-retry" not in p.name).read_text())["argv"]
    assert "previous attempt" not in first[first.index("-p") + 1]
    no_worktrees_left(loaded)
    check_ok(loaded)


def test_ownership_violation_twice_fails_and_stops_after_extract(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "outside:extract@alpha-8k:2")
    assert build(loaded) == 2
    st = state_of(loaded)
    assert not st["finished"] and "extraction failed" in st["stopped"]
    assert "check" not in st["phases_done"] and "extract" not in st["phases_done"]
    assert not (loaded.wiki / "entities" / "intruder.md").exists()
    no_worktrees_left(loaded)


def test_resume_after_failure_reruns_only_unfinished(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "fail:check@alpha-8k:2")
    assert build(loaded) == 2
    st = state_of(loaded)
    done_before = {k for k, t in st["tasks"].items() if t["status"] == "done"}
    failed = {k for k, t in st["tasks"].items() if t["status"] == "failed"}
    assert failed and all(k.startswith("check") for k in failed)
    assert "extract" in st["phases_done"]
    runs_before = len(Ledger(loaded.ledger_path).entries())
    with pytest.raises(WikiError, match="--resume"):
        build(loaded)
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert build(loaded, resume=True) == 0
    st = state_of(loaded)
    assert st["finished"]
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:]]
    assert not any(t.startswith("extract") for t in rerun)
    assert set(t.removesuffix("-retry") for t in rerun if t.startswith("check")) == failed
    assert done_before <= {k for k, t in st["tasks"].items() if t["status"] == "done"}
    check_ok(loaded)


def test_budget_stop_is_not_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "overcap:resolve")
    assert build(loaded) == 2
    st = state_of(loaded)
    assert st["tasks"]["resolve"]["attempts"] == 1 and st["tasks"]["resolve"]["status"] == "failed"
    assert "error_max_budget_usd" in st["tasks"]["resolve"]["reason"]


def test_turn_limit_is_not_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "maxturns:link")
    assert build(loaded) == 2
    assert state_of(loaded)["tasks"]["link"]["attempts"] == 1


def test_no_result_is_charged_full_cap(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "noresult:index:1")
    assert build(loaded) == 0  # retried once and passed
    lines = [e for e in Ledger(loaded.ledger_path).entries() if e["task_id"] == "index"]
    assert lines[0]["outcome"] == "no_result" and lines[0]["charged_full_cap"]
    assert lines[0]["cost_usd"] == pytest.approx(lines[0]["run_cap_usd"], rel=1e-3)


def test_compile_edit_outside_markers_fails(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "markers:compile")
    assert build(loaded) == 2
    st = state_of(loaded)
    t = st["tasks"]["compile-01"]
    assert t["status"] == "failed" and "outside its current-picture markers" in t["reason"]
    assert "An edit outside the markers" not in (loaded.wiki / "entities" / "acme.md").read_text()
    no_worktrees_left(loaded)


def test_editing_an_input_file_stops_the_build(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "input:extract:1")
    assert build(loaded) == 2
    assert "read-only input" in state_of(loaded)["stopped"]
    no_worktrees_left(loaded)


def test_editing_a_document_stops_the_build(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "input:resolve:1")
    assert build(loaded) == 2
    assert "read-only input" in state_of(loaded)["stopped"] or "changed" in state_of(loaded)["stopped"]


def test_bad_tools_fail_the_run(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "badtools:index")
    assert build(loaded) == 2
    assert state_of(loaded)["tasks"]["index"]["attempts"] == 1
    assert "bad_tools" in state_of(loaded)["tasks"]["index"]["reason"]


def test_schema_edit_is_caught(loaded):
    assert build(loaded) == 0
    (loaded.wiki / "_schema.md").write_text("edited")
    assert main(["-w", str(loaded.root), "check"]) == 1


def test_second_command_exits_while_lock_held(loaded):
    code = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from wiki.workspace import Workspace\n"
        "with Workspace(%r).lock('test'):\n"
        "    print('locked', flush=True); time.sleep(20)\n"
    ) % (str(os.path.join(os.path.dirname(__file__), "..", "src")), str(loaded.root))
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert main(["-w", str(loaded.root), "build"]) == 75
        assert main(["-w", str(loaded.root), "add", str(loaded.root / "wiki.toml")]) == 75
        assert main(["-w", str(loaded.root), "status"]) == 0  # read-only commands need no lock
    finally:
        holder.kill()
        holder.wait()
    assert main(["-w", str(loaded.root), "build", "--estimate"]) == 0


def test_stale_lock_is_taken_over(loaded):
    loaded.lock_path.write_text(json.dumps({"pid": 999_999, "command": "build"}))
    with loaded.lock("build"):
        pass
    assert not loaded.lock_path.exists()


def test_resumed_task_is_told_why_it_failed(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "outside:extract@alpha-8k:2")
    assert build(loaded) == 2
    failed_id = next(k for k, t in state_of(loaded)["tasks"].items() if t["status"] == "failed")
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert build(loaded, resume=True) == 0
    argv = json.loads(next(loaded.runs.glob(f"build-*/{failed_id}.cmd.json")).read_text())["argv"]
    assert "previous attempt was rejected" in argv[argv.index("-p") + 1]


def test_unspent_cap_moves_to_unfinished_tasks(ws):
    from wiki.phases import Context

    ctx = Context(ws, "build", "build-x", ws.repo, shares={"extract-b01": 4.0, "link": 0.5, "index": 0.5}, cap=10.0)
    ctx.state = {"tasks": {"extract-b01": {"status": "done"}}}
    ctx.rebalance()  # nothing spent on the ledger for build-x: half of 10 * 0.9 for link and index
    assert ctx.shares["link"] == pytest.approx(2.25) and ctx.shares["index"] == pytest.approx(2.25)
    assert ctx.shares["extract-b01"] == 4.0


def test_check_that_rewords_a_filed_line_is_rejected_and_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "reword:check:1")
    assert build(loaded) == 0
    check_ok(loaded)
    vio = sorted(loaded.runs.glob("build-*/check-*.violations.txt"))
    assert len(vio) == 1 and "may only gain valid_to or retracted" in vio[0].read_text()
    assert "Reworded" not in "".join(p.read_text() for p in (loaded.wiki / "notes").glob("*.md"))
    # the retry is told why: the problem text is in its task
    retry = json.loads(sorted(loaded.runs.glob("build-*/check-*-retry.cmd.json"))[0].read_text())
    assert "file the correction as a new line in addenda" in " ".join(retry["argv"])


def test_compile_picture_with_a_plain_line_is_rejected_and_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "plainpic:compile:1")
    assert build(loaded) == 0
    vio = sorted(loaded.runs.glob("build-*/compile-*.violations.txt"))
    assert len(vio) == 1 and "must be a list item" in vio[0].read_text()
    findings = run_checks(loaded.wiki, Manifest.load(loaded.manifest_path), repo=loaded.repo)
    assert not [f for f in findings if f.rule == "picture"], [str(f) for f in findings if f.rule == "picture"]


def test_index_catalog_leaves_out_retracted_red_flags(loaded):
    from wiki.phases import Context, catalog

    assert build(loaded) == 0
    flagged = [p for p in sorted((loaded.wiki / "notes").glob("*.md")) if "## red-flags\n- " in p.read_text()]
    assert flagged
    p = flagged[0]
    text = p.read_text()
    line = text.split("## red-flags\n", 1)[1].splitlines()[0]
    p.write_text(text.replace(line, line[:-1] + " | retracted: 2024-12-31]", 1))
    cat = catalog(Context(loaded, "test", "catalog-test", loaded.repo))
    assert line[:-1] not in cat  # neither the line nor its retracted form
    others = [x for q in flagged[1:] for x in q.read_text().split("## red-flags\n", 1)[1].splitlines()[:1]]
    assert all(x in cat for x in others)


def test_a_resumed_compile_reruns_the_failed_group_under_its_own_id(loaded, monkeypatch):
    from conftest import set_runner
    from wiki.compile import compile_pages

    set_runner(loaded, compile_group=2, concurrency=1)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "fail:compile@entities/gamma-lenders.md")
    assert build(loaded) == 2
    groups = state_of(loaded)["compile_groups"]
    failed = next(t for t, pages in groups.items() if "gamma-lenders" in pages)
    assert failed != "compile-01" and state_of(loaded)["tasks"][failed]["status"] == "failed"
    runs_before = len(Ledger(loaded.ledger_path).entries())
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert build(loaded, resume=True) == 0
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:] if e["phase"] == "compile"]
    assert rerun == [failed]  # not a new compile-01 that the state calls done
    page = (loaded.wiki / "entities" / "gamma-lenders.md").read_text()
    assert "- Gamma Lenders provide a $2.3B term loan." in page.split("<!-- current-picture:end -->")[0]
    m = Manifest.load(loaded.manifest_path)
    assert compile_pages(loaded.wiki, {s: r.get("facts_sha") for s, r in m.entities.items()}).needs_picture == []
    check_ok(loaded)


def test_a_picture_a_compile_run_writes_links_slugs(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "aliaslink")
    assert build(loaded) == 0
    page = (loaded.wiki / "entities" / "acme.md").read_text()
    assert "- Also filed as [[acme]]." in page and "[[acme-co]]" not in page
    assert "[[acme-co]]" not in gitops.git(loaded.repo, "show", "HEAD:wiki/entities/acme.md")
    check_ok(loaded)


def test_resolve_that_rewords_a_filed_line_is_rejected_and_retried(loaded, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "reword:resolve:1")
    assert build(loaded) == 0
    vio = sorted(loaded.runs.glob("build-*/resolve.violations.txt"))
    assert len(vio) == 1 and "may only gain valid_to or retracted" in vio[0].read_text()
    assert "Rechecked" not in "".join(p.read_text() for p in (loaded.wiki / "addenda").glob("*.md"))
    check_ok(loaded)


def test_a_retry_hears_every_kind_of_problem():
    from wiki.phases import retry_reason

    changed = [f"wiki/contracts/x.md: filed line {n} was changed or removed: '- q {n}'" for n in range(13, 21)]
    lint = ["ERROR addenda/x.md: line 5: fact line without a valid source tag: '- y' [tag]"]
    reason = retry_reason(changed + lint)
    assert "filed line 13" in reason and "fact line without a valid source tag" in reason
    assert reason.endswith("(6 more of these)")
    assert retry_reason(["a.md: one", "a.md: two"]) == "a.md: one; a.md: two"
    # a quoted line holding both quote kinds is a repr with escapes; it is still one kind of problem
    words = ["early", "late", "twice", "often", "again", "once", "today", "soon"]
    both = [f"wiki/notes/x.md: filed line {n} was changed or removed: " + repr(f"- Acme's \"Board\" met {w}.")
            for n, w in enumerate(words, start=1)]
    tag = "ERROR addenda/x.md: line 6: malformed source tag '[source: u' [tag]"
    reason = retry_reason(both + lint + [tag])
    assert "fact line without a valid source tag" in reason and "malformed source tag" in reason
    # the note fits the retry's room, and says how many problems it leaves out
    long = [f"wiki/notes/x.md: filed line {n} was changed or removed: {'x' * 900!r}" for n in range(10)] + lint
    cut = retry_reason(long, limit=2000)
    assert len(cut) <= 2000 and "fact line without a valid source tag" in cut and cut.endswith("(9 more of these)")
    # a long problem early in the list does not silence the short kinds after it
    shape = ["wiki/notes/x.md: filed line 1 was changed or removed: " + "s" * 275,
             "addenda/x.md: frontmatter name " + "n" * 1750, lint[0], tag,
             "ERROR addenda/x.md: line 7: tag names unit 'zz', not this file's unit [tag]"]
    reason = retry_reason(shape)
    assert len(reason) <= 2000 and reason.endswith("(1 more of these)") and "frontmatter" not in reason
    assert all(k in reason for k in ("filed line 1", "fact line without", "malformed source tag", "names unit"))
    # when no problem fits whole, the first is cut and the count stays whole
    reason = retry_reason(["a.md: " + "x" * 1984] * 3)
    assert len(reason) == 2000 and reason.endswith("x ... (2 more of these)")
    assert retry_reason(["x" * 2500]) == "x" * 2000
    # a list that fits whole is told whole, though its first problem with a count after it would not fit
    whole = ["a.md: " + "x" * 1983, "b.md: two"]
    assert retry_reason(whole) == "; ".join(whole)
    # with room for the count alone, the note is the count, and it counts every problem
    assert retry_reason(["a" * 50, "b" * 50], limit=22) == " ... (2 more of these)"
    # a link is part of a problem's text, not of its kind
    pages = [f"ERROR   _index.md: entity page [[filler-{c}]] is not in the index [index]" for c in "abcdefgh"]
    assert "unit 'beta-note'" in retry_reason(pages + ["ERROR   _index.md: unit 'beta-note' is not in the index [index]"])


def test_a_rejected_picture_names_every_problem_it_has(tmp_path):
    from wiki.grammar import MARK_END, MARK_START
    from wiki.phases import WriterTask, ownership_problems

    repo = tmp_path / "repo"
    (repo / "wiki" / "entities").mkdir(parents=True)
    page = repo / "wiki" / "entities" / "acme.md"
    page.write_text(f"# acme\n{MARK_START}\n{MARK_END}\n## Sources\n")
    gitops.init_repo(repo)
    gitops.commit_all(repo, "base")
    facts = "".join(f"- Fact {i}. [source: u1 | as-of: 2024-01-01]\n" for i in range(31))
    page.write_text(f"# acme\n{MARK_START}\n{facts}Sections: From u1.\n{MARK_END}\n## Sources\n")
    task = WriterTask("compile-01", "compile", "compile", "compile", "", marker_pages={"entities/acme.md"})
    problems = ownership_problems(None, task, repo, gitops.head(repo), ["wiki/entities/acme.md"])
    assert len(problems) == 2 and "over 30 lines" in problems[0] and "must be a list item" in problems[1]


def test_a_compile_run_may_write_30_picture_lines_but_not_31(tmp_path):
    from wiki.grammar import MARK_END, MARK_START
    from wiki.phases import WriterTask, ownership_problems

    repo = tmp_path / "repo"
    (repo / "wiki" / "entities").mkdir(parents=True)
    page = repo / "wiki" / "entities" / "acme.md"
    page.write_text(f"# acme\n{MARK_START}\n{MARK_END}\n## Sources\n")
    gitops.init_repo(repo)
    gitops.commit_all(repo, "base")
    task = WriterTask("compile-01", "compile", "compile", "compile", "", marker_pages={"entities/acme.md"})
    for n, want in ((30, 0), (31, 1)):
        facts = "".join(f"- Fact {i}. [source: u1 | as-of: 2024-01-01]\n" for i in range(n))
        page.write_text(f"# acme\n{MARK_START}\n{facts}{MARK_END}\n## Sources\n")
        assert len(ownership_problems(None, task, repo, gitops.head(repo), ["wiki/entities/acme.md"])) == want


class HardStop(RuntimeError):
    pass


def test_a_build_stopped_hard_in_compile_resumes_only_the_groups_left(loaded, monkeypatch):
    from conftest import set_runner
    from wiki import phases

    set_runner(loaded, compile_group=2, concurrency=1)
    real = phases.execute

    def execute(ctx, task):
        if task.task_id == "compile-02":
            raise HardStop("stopped hard")  # as a Ctrl-C or a git failure in a worker would stop it
        return real(ctx, task)

    monkeypatch.setattr(phases, "execute", execute)
    with pytest.raises(HardStop):
        build(loaded)
    monkeypatch.setattr(phases, "execute", real)
    st = state_of(loaded)
    groups = st["compile_groups"]
    left = sorted(t for t in groups if st["tasks"][t]["status"] != "done")
    assert len(groups) == 3 and "compile-02" in left and len(left) < 3
    m = Manifest.load(loaded.manifest_path)
    for t in set(groups) - set(left):  # recorded as each group merged, before the stop
        assert all(m.entities[s]["facts_sha"] for s in groups[t])
    runs_before = len(Ledger(loaded.ledger_path).entries())
    assert build(loaded, resume=True) == 0
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:] if e["phase"] == "compile"]
    assert rerun == left  # the groups that finished before the stop are not paid for again
    assert len(state_of(loaded)["compile_groups"]) == 3
    check_ok(loaded)


def test_a_resumed_compile_gives_a_done_groups_blank_page_a_new_group(loaded, monkeypatch):
    from conftest import set_runner
    from wiki.grammar import MARK_END, MARK_START

    set_runner(loaded, compile_group=2, concurrency=1)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "fail:compile@entities/gamma-lenders.md")
    assert build(loaded) == 2
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    st = state_of(loaded)
    groups = st["compile_groups"]
    done = sorted(t for t in groups if st["tasks"][t]["status"] == "done")
    failed = next(t for t in groups if "gamma-lenders" in groups[t])
    blank = groups[done[0]][0]  # a page its done group left without a picture
    p = loaded.wiki / "entities" / f"{blank}.md"
    text = p.read_text()
    p.write_text(text[:text.index(MARK_START) + len(MARK_START)] + "\n" + text[text.index(MARK_END):])
    gitops.commit_all(loaded.repo, "a done run left a page blank")
    runs_before = len(Ledger(loaded.ledger_path).entries())
    assert build(loaded, resume=True) == 0
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:] if e["phase"] == "compile"]
    new = f"compile-{len(groups) + 1:02d}"
    assert rerun == [failed, new] and not set(rerun) & set(done)
    after = state_of(loaded)["compile_groups"]
    assert after[new] == [blank] and all(after[t] == groups[t] for t in done)
    assert "[source:" in p.read_text().split(MARK_END)[0]
    check_ok(loaded)


def test_compile_groups_that_merge_at_once_each_record_their_pages(loaded, monkeypatch):
    """At concurrency 4 the groups' after_merge hooks may run at once. Here a hook that has loaded the manifest
    waits up to half a second for a second hook to load it too, as it could without the lock; each still records
    its pages, so a hard stop on the last group reruns only that group."""
    import threading
    import traceback

    from conftest import set_runner
    from wiki import phases

    set_runner(loaded, compile_group=1, concurrency=4)
    real_execute, real_parallel, real_load, real_save = phases.execute, phases.run_parallel, Manifest.load, Manifest.save
    last = {}
    window = threading.Condition()
    inside = [0]  # hooks between their manifest load and save

    def in_hook() -> bool:
        return any(f.name == "after_merge" for f in traceback.extract_stack())

    def run_parallel(ctx, tasks):
        if tasks and tasks[0].phase == "compile":
            last["id"] = tasks[-1].task_id
        return real_parallel(ctx, tasks)

    def execute(ctx, task):
        if task.task_id == last.get("id"):
            raise HardStop("stopped hard")
        return real_execute(ctx, task)

    def load(path):
        m = real_load(path)
        if in_hook():
            with window:
                inside[0] += 1
                window.notify_all()
                window.wait_for(lambda: inside[0] >= 2, timeout=0.5)
        return m

    def save(self, path):
        real_save(self, path)
        if in_hook():
            with window:
                inside[0] -= 1

    with monkeypatch.context() as m:
        m.setattr(phases, "run_parallel", run_parallel)
        m.setattr(phases, "execute", execute)
        m.setattr(Manifest, "load", staticmethod(load))
        m.setattr(Manifest, "save", save)
        with pytest.raises(HardStop):
            build(loaded)
    st = state_of(loaded)
    groups = st["compile_groups"]
    done = sorted(t for t in groups if st["tasks"][t]["status"] == "done")
    assert len(done) >= 3 and last["id"] not in done
    entities = Manifest.load(loaded.manifest_path).entities  # two saves at once could also leave it unreadable
    assert [s for t in done for s in groups[t] if not (entities.get(s) or {}).get("facts_sha")] == []
    runs_before = len(Ledger(loaded.ledger_path).entries())
    assert build(loaded, resume=True) == 0
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:] if e["phase"] == "compile"]
    assert rerun == [last["id"]]
    check_ok(loaded)


def test_a_failed_compile_group_records_nothing_and_runs_again_on_resume(loaded, tmp_path, monkeypatch):
    from conftest import set_runner
    from wiki.grammar import MARK_END

    set_runner(loaded, compile_group=2, concurrency=1)
    assert build(loaded) == 0
    page = loaded.wiki / "entities" / "acme.md"
    sha = Manifest.load(loaded.manifest_path).entities["acme"]["facts_sha"]
    picture = page.read_text().split(MARK_END)[0]
    assert "[source:" in picture  # a picture already there, so only a new facts_sha can send acme to Compile
    docs = tmp_path / "more"
    docs.mkdir()
    (docs / "aaa_update.md").write_text("ENTITY: acme | company | Acme\nFACT: acme | Acme moved its record date.\n")
    add(loaded, [docs])
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "fail:compile@entities/acme.md")
    assert build(loaded) == 2
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert Manifest.load(loaded.manifest_path).entities["acme"]["facts_sha"] == sha  # the failed run recorded nothing
    st = state_of(loaded)
    failed = next(t for t, pages in st["compile_groups"].items() if "acme" in pages)
    assert st["tasks"][failed]["status"] != "done"
    runs_before = len(Ledger(loaded.ledger_path).entries())
    assert build(loaded, resume=True) == 0
    rerun = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[runs_before:] if e["phase"] == "compile"]
    assert failed in rerun
    now = page.read_text().split(MARK_END)[0]
    assert now != picture and "aaa-update" in now
    check_ok(loaded)


def test_the_retry_of_a_rejected_run_hears_every_kind_of_problem(loaded):
    import dataclasses

    from wiki import phases

    assert build(loaded) == 0
    ctx = phases.Context(loaded, "build", "build-retry-note", loaded.repo)
    ctx.state = {"tasks": {}}
    task = phases.compile_tasks(ctx, ["acme"])[0]
    calls = []

    def validate(wiki, changed):  # rejects the first run for two kinds of problem, in the validators' order
        calls.append(changed)
        if len(calls) > 1:
            return []
        return [f"wiki/notes/x.md: filed line {n} was changed or removed: '- q {n}'" for n in range(1, 5)] + [
            "ERROR addenda/x.md: line 5: fact line without a valid source tag: '- y' [tag]"]

    out = phases.execute(ctx, dataclasses.replace(task, validate=validate))
    assert out.ok and out.attempts == 2
    argv = (ctx.run_dir / f"{task.task_id}-retry.cmd.json").read_text()
    assert "fact line without a valid source tag" in argv and "filed line 1" in argv
    long_calls = []

    def validate_long(wiki, changed):  # rejects the first run at more length than the retry note has room for
        long_calls.append(changed)
        if len(long_calls) > 1:
            return []
        return [f"wiki/notes/x.md: filed line {n} was changed or removed: {'x' * 940!r}" for n in range(10)] + [
            "ERROR addenda/x.md: line 5: fact line without a valid source tag: '- y' [tag]"]

    long_task = dataclasses.replace(task, task_id=f"{task.task_id}-long", validate=validate_long)
    assert phases.execute(ctx, long_task).ok
    argv = json.loads((ctx.run_dir / f"{long_task.task_id}-retry.cmd.json").read_text())["argv"]
    note = argv[argv.index("-p") + 1].split(phases.RETRY_NOTE)[-1]
    assert len(note) <= phases.RETRY_NOTE_CHARS and note.endswith("(9 more of these)")


def test_ctrl_c_stops_a_build_without_a_retry_or_a_new_task_and_resume_finishes_it(loaded, monkeypatch):
    """After Ctrl-C a build used to retry each run that was cut short, and never came back to the prompt."""
    from conftest import interrupted, set_runner

    set_runner(loaded, concurrency=2)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "hang:extract")
    started = lambda: [p for p in loaded.runs.glob("build-*/extract-*.jsonl") if p.stat().st_size]  # noqa: E731
    start = time.monotonic()
    why = interrupted(lambda: build(loaded), lambda: len(started()) >= 2)
    assert isinstance(why, KeyboardInterrupt) and time.monotonic() - start < 40
    lines = Ledger(loaded.ledger_path).entries()
    assert len(lines) == 2 and all(e["outcome"] == "interrupted" and e["charged_full_cap"] for e in lines)
    assert not any(e["task_id"].endswith("-retry") for e in lines)
    tasks = state_of(loaded)["tasks"]
    cut = sorted(t for t, rec in tasks.items() if rec["status"] == "failed")
    assert cut == sorted(e["task_id"] for e in lines) and all(tasks[t]["reason"] == "run ended: interrupted" for t in cut)
    assert len(tasks) == 4 and all(rec["status"] == "pending" for t, rec in tasks.items() if t not in cut)
    no_worktrees_left(loaded)
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert build(loaded, resume=True) == 0
    check_ok(loaded)


def test_a_compile_run_that_leaves_a_picture_empty_is_rejected_and_retried(loaded, monkeypatch):
    """Such a run used to count as done, and its pages were paid for again by the next build."""
    from wiki.grammar import picture_text

    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "blankpic:compile:1")
    assert build(loaded) == 0
    compiles = [e["task_id"] for e in Ledger(loaded.ledger_path).entries() if e["phase"] == "compile"]
    assert compiles == ["compile-01", "compile-01-retry"]
    violations = next(loaded.runs.glob("build-*/compile-01.violations.txt")).read_text()
    assert "its current picture is empty" in violations and violations.count("\n") == len(list((loaded.wiki / "entities").glob("*.md")))
    assert all(picture_text(p.read_text()).strip() for p in (loaded.wiki / "entities").glob("*.md"))
    check_ok(loaded)
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "blankpic:compile")  # every attempt: the group fails, and is not recorded
    (loaded.root / "more").mkdir()
    (loaded.root / "more" / "late_note.md").write_text("ENTITY: acme | company | Acme\nFACT: acme | A late fact.\n")
    add(loaded, [loaded.root / "more"])
    assert build(loaded) == 2
    assert "compile" in state_of(loaded)["stopped"]


def test_add_refuses_before_it_writes_anything(loaded, tmp_path):
    """One file that could not be converted, or two that make one slug, used to stop wiki add with the earlier
    documents already replaced in docs/ and the manifest not saved."""
    from wiki.workspace import sha256_file

    def in_step():
        m = Manifest.load(loaded.manifest_path)
        return all(sha256_file(loaded.docs / f"{u}.txt") == m.units[u]["sha256"] for u in m.live_units())

    before = Manifest.load(loaded.manifest_path).data
    new = tmp_path / "new"
    new.mkdir()
    (new / "alpha_8k.txt").write_text((loaded.docs / "alpha-8k.txt").read_text() + "FACT: acme | One more line.\n")
    (new / "sheet.xyz").write_text("not a supported type")
    with pytest.raises(WikiError, match="unsupported type"):
        add(loaded, [new / "alpha_8k.txt", new / "sheet.xyz"])
    assert in_step() and Manifest.load(loaded.manifest_path).data == before
    (new / "alpha_8k.md").write_text("the same slug as alpha_8k.txt")
    (new / "omega.txt").write_text("FACT: acme | Omega.\n")
    with pytest.raises(WikiError, match="both make the slug"):
        add(loaded, [new / "omega.txt", new / "alpha_8k.txt", new / "alpha_8k.md"])
    assert in_step() and Manifest.load(loaded.manifest_path).data == before and not (loaded.docs / "omega.txt").exists()
    with pytest.raises(WikiError, match="both make the slug"):  # the manifest's alpha_8k.txt and this alpha_8k.md
        add(loaded, [new / "omega.txt", new / "alpha_8k.md"])
    assert in_step() and Manifest.load(loaded.manifest_path).data == before and not (loaded.docs / "omega.txt").exists()
    assert add(loaded, [new / "alpha_8k.txt"]) == 0
    assert in_step() and Manifest.load(loaded.manifest_path).units["alpha-8k"]["status"] == "changed"


def test_a_stale_manifest_tmp_goes_when_the_lock_is_taken(loaded):
    tmp = loaded.repo / "manifest.json.tmp"
    tmp.write_text("{half a manifest")  # a manifest write that was killed
    with loaded.lock("add"):
        assert not tmp.exists()
    assert gitops.is_clean(loaded.repo)


def test_a_retry_note_takes_no_time_over_a_long_run_of_characters_without_spaces():
    from wiki.phases import retry_reason

    start = time.monotonic()  # sorting problems into kinds took 9 s on a run of 60,000 non-spaces, 4 times that on twice
    note = retry_reason(["wiki/notes/u.md: line 3: " + "x" * 120_000 + " is not a fact line", "wiki/notes/u.md: no tag"])
    assert time.monotonic() - start < 2 and len(note) <= 2000 and "no tag" in note


def test_a_kill_or_a_closed_terminal_ends_a_build_as_ctrl_c_does(loaded):
    """SIGTERM and SIGHUP used to end wiki with no ledger line for the runs in flight, the runs still going, and
    the lock left behind."""
    import signal

    from conftest import set_runner

    set_runner(loaded, concurrency=2)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": os.path.join(root, "src"), "WIKI_FAKE_MISBEHAVE": "hang:extract"}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        before = len(Ledger(loaded.ledger_path).entries())
        args = ["build", "--resume"] if before else ["build"]
        proc = subprocess.Popen([sys.executable, "-m", "wiki.cli", "-w", str(loaded.root), *args], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 30
        streams = lambda: [p for p in loaded.runs.glob("build-*/extract-*.jsonl") if p.stat().st_size]  # noqa: E731
        while time.monotonic() < deadline and (len(streams()) < 2 or not loaded.lock_path.exists()):
            time.sleep(0.05)
        time.sleep(0.3)
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 130 and "wiki: interrupted" in err, (sig, proc.returncode, err[-300:])
        new = Ledger(loaded.ledger_path).entries()[before:]
        assert len(new) == 2 and all(e["outcome"] == "interrupted" and e["charged_full_cap"] for e in new), (sig, new)
        assert not loaded.lock_path.exists()
        for p in loaded.runs.glob("build-*/extract-*.jsonl"):  # the next round waits for streams of its own
            p.unlink()
    assert build(loaded, resume=True) == 0
    check_ok(loaded)


def test_a_resume_says_what_it_may_spend_and_takes_a_lower_cap(loaded, monkeypatch, capsys):
    """wiki build --resume printed no figure, and ignored --max-usd."""
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "maxturns:check")
    assert build(loaded) == 2
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    spent = Ledger(loaded.ledger_path).spent()
    capsys.readouterr()
    assert build(loaded, resume=True, estimate_only=True) == 0
    out = capsys.readouterr().out
    assert f"Spent so far ${spent:,.2f} of its cap of $6.00" in out and "unfinished tasks' budgets come to $" in out
    assert Ledger(loaded.ledger_path).spent() == spent
    assert build(loaded, resume=True, max_usd=spent + 0.01) == 2  # no task's budget fits under that cap
    assert "the command's cap of" in state_of(loaded)["stopped"] and Ledger(loaded.ledger_path).spent() == spent
    assert build(loaded, resume=True) == 0
    check_ok(loaded)


def test_a_document_added_again_unchanged_on_a_later_day_stays_unchanged(loaded, tmp_path, monkeypatch):
    """A document with no [SOURCE] line of its own gets "added: <today>" in its text. Added again on a later day
    it used to count as changed, and the next build paid to extract and check it again."""
    import datetime

    from wiki import convert as convert_mod

    assert build(loaded) == 0
    stored = (loaded.docs / "beta-note.txt").read_text()
    today = datetime.date.today()
    assert stored.startswith(f"[SOURCE: beta_note.md | added: {today.isoformat()}]")

    class Tomorrow(datetime.date):
        @classmethod
        def today(cls):
            return today + datetime.timedelta(days=1)

    monkeypatch.setattr(convert_mod, "date", Tomorrow)
    corpus = loaded.root.parent / "corpus"
    assert add(loaded, [corpus]) == 0
    m = Manifest.load(loaded.manifest_path)
    assert m.pending_units() == [] and (loaded.docs / "beta-note.txt").read_text() == stored
    (corpus / "beta_note.md").write_text((corpus / "beta_note.md").read_text() + "FACT: acme | One more.\n")
    add(loaded, [corpus])  # changed for real: filed again, under the day it changed
    assert Manifest.load(loaded.manifest_path).pending_units() == ["beta-note"]
    assert (loaded.docs / "beta-note.txt").read_text().startswith(
        f"[SOURCE: beta_note.md | added: {(today + datetime.timedelta(days=1)).isoformat()}]")


def test_two_files_of_one_name_and_hidden_folders(loaded, tmp_path):
    """report.txt in two folders was taken for two versions of one document: the second replaced the first."""
    inbox = tmp_path / "inbox"
    for year in ("2023", "2024"):
        (inbox / year).mkdir(parents=True)
        (inbox / year / "report.txt").write_text(f"FACT: acme | Revenue in {year}.\n")
    before = Manifest.load(loaded.manifest_path).data
    with pytest.raises(WikiError, match="both make the slug 'report'"):
        add(loaded, [inbox])
    assert Manifest.load(loaded.manifest_path).data == before and not (loaded.docs / "report.txt").exists()
    (inbox / "2023" / "report.txt").rename(inbox / "2023" / "report_2023.txt")
    (inbox / ".cache").mkdir()
    (inbox / ".cache" / "hidden.txt").write_text("FACT: acme | Not a document.\n")  # used to be added
    assert add(loaded, [inbox]) == 0
    assert {"report", "report-2023"} <= set(Manifest.load(loaded.manifest_path).units)
    assert "hidden" not in Manifest.load(loaded.manifest_path).units


def test_a_document_added_again_before_a_resume_is_extracted_again(loaded, corpus, monkeypatch):
    """The resumed build took its finished tasks from state.json, so it filed the corrected document under its new
    sha256 with the facts of the old text."""
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "maxturns:check")
    assert build(loaded) == 2
    monkeypatch.delenv("WIKI_FAKE_MISBEHAVE")
    assert "extract" in state_of(loaded)["phases_done"]
    (corpus / "beta_note.md").write_text((corpus / "beta_note.md").read_text().replace("$46.50", "$48.00"))
    add(loaded, [corpus])
    before = len(Ledger(loaded.ledger_path).entries())
    assert build(loaded, resume=True) == 0
    extracted = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()[before:] if e["phase"] == "extract"]
    batch = next(b for b, us in state_of(loaded)["batches"].items() if "beta-note" in us)
    assert extracted == [f"extract-{batch}"]  # that batch only
    notes = (loaded.wiki / "notes" / "beta-note.md").read_text()
    assert "$48.00" in notes and "$46.50" not in notes
    assert Manifest.load(loaded.manifest_path).pending_units() == []
    check_ok(loaded)


def test_link_may_not_give_a_slug_a_second_row_and_index_must_write_entries_the_program_can_split(loaded, monkeypatch):
    """Both passed their run's checks and failed the check that ends the build, after every later phase was paid
    for; the build could then not be finished, since a resume had nothing left to run."""
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "dupslug:link:1;bigesc:index:1")
    assert build(loaded) == 0
    runs = [e["task_id"] for e in Ledger(loaded.ledger_path).entries()]
    assert runs[runs.index("link"):runs.index("link") + 2] == ["link", "link-retry"] and runs[-2:] == ["index", "index-retry"]
    assert "has more than one row" in next(loaded.runs.glob("build-*/link.violations.txt")).read_text()
    assert "no entry starts with a `### ` heading" in next(loaded.runs.glob("build-*/index.violations.txt")).read_text()
    check_ok(loaded)


def test_a_run_id_is_never_shared(tmp_path, monkeypatch):
    from wiki import phases

    stamps = iter(["20260101T000000Z", "20260101T000000Z", "20260101T000001Z"])
    monkeypatch.setattr(phases, "now_id", lambda: next(stamps))
    assert phases.new_id(lambda ts: ts == "20260101T000000Z") == "20260101T000001Z"  # it waited for a free one


def test_a_file_over_the_page_limit_is_its_runs_problem(loaded):
    """No run's checks looked at size, so a per-unit file over 100 KB failed only the check that ends the build."""
    from wiki.phases import lint_changed

    assert build(loaded) == 0
    notes = loaded.wiki / "notes" / "alpha-8k.md"
    notes.write_text(notes.read_text() + "".join(f"- Line {n}. [source: alpha-8k | as-of: 2024-05-13]\n" for n in range(2500)))
    problems = lint_changed(Manifest.load(loaded.manifest_path), loaded.wiki, ["wiki/notes/alpha-8k.md"])
    assert len(problems) == 1 and "over the 100 KB limit" in problems[0]
    assert lint_changed(Manifest.load(loaded.manifest_path), loaded.wiki, ["wiki/notes/beta-note.md"]) == []
