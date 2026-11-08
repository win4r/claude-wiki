"""Offline end to end: wiki build over the fixture corpus, resume, incremental builds, and every guard."""

import json
import os
import subprocess
import sys

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
    assert "<!-- current-picture:start -->\n- " in acme and "## From alpha-8k" in acme and "## From beta-note" in acme
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
