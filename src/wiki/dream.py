"""wiki dream: turn what ask sessions struggled with into fixes, on a review branch the owner accepts or discards."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import WikiError, gitops
from . import check as check_mod
from . import cost as cost_mod
from .compile import split_escalations
from .ledger import Ledger
from .phases import (
    Context, WriterTask, index_task, input_folder, lint_changed, link_task, new_id, phase_compile, run_parallel,
    survival_problems,
)
from .prompts import render, write_prompt
from .runner import Runner, RunSpec, run_all
from .workspace import make_read_only, private_dir, remove_tree

TAG_PREFIX = "dream-accepted-"


def _ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def last_accepted(ws) -> datetime | None:
    stamps = [_ts(t[len(TAG_PREFIX):]) for t in gitops.tags(ws.repo, TAG_PREFIX + "*")]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def collect_transcripts(ws) -> list[Path]:
    """Ask transcripts since the newest dream-accepted tag. Eval transcripts live elsewhere and are never used."""
    since = last_accepted(ws)
    out = []
    for p in sorted((ws.runs / "ask").glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except ValueError:
            continue
        if rec.get("command") != "ask":
            continue
        t = datetime.fromisoformat(rec["time"]) if rec.get("time") else None
        if since is None or (t and t > since):
            out.append(p)
    return out


RESULT_LINES = 8  # of each tool result shown to the analysts
RESULT_LINE_CHARS = 200
TOOL_INPUTS = ("file_path", "pattern", "path", "glob", "type", "output_mode", "-i", "-n", "-o", "-A", "-B", "-C",
               "context", "multiline", "head_limit", "offset", "limit")


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def reader_steps(stream: Path) -> list[str]:
    """What a reader did, from its stream: each tool call with its inputs, and the start of what came back."""
    if not stream.is_file():
        return ["(stream not found)"]
    steps: list[list[str]] = []  # one entry per tool call: the call line, then its result lines
    by_id: dict[str, list[str]] = {}
    # split on newlines only: a document line separator (U+2028) inside a JSON line must not cut the event
    for raw in stream.read_text(errors="replace").split("\n"):
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") not in ("assistant", "user"):
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                inp = block.get("input") or {}
                args = ", ".join(f"{k}={json.dumps(inp[k], ensure_ascii=False)}" for k in TOOL_INPUTS if k in inp)
                step = [f"- {block.get('name')}({args})"]
                steps.append(step)
                by_id[str(block.get("id"))] = step
            elif block.get("type") == "tool_result":
                rows = _result_text(block.get("content")).strip("\n").splitlines()
                shown = ["    > (error)"] if block.get("is_error") else []
                shown += [f"    > {r[:RESULT_LINE_CHARS]}" for r in rows[:RESULT_LINES]]
                if len(rows) > RESULT_LINES:
                    shown.append(f"    > ({len(rows) - RESULT_LINES} more line(s))")
                step = by_id.get(str(block.get("tool_use_id")))
                if step is None:  # a result whose call is not in the stream
                    step = ["- (a tool call)"]
                    steps.append(step)
                step += shown or ["    > (empty)"]
    return [ln for step in steps for ln in step] or ["(no tool calls)"]


def transcript_markdown(path: Path) -> str:
    rec = json.loads(path.read_text())
    flags = []
    if rec.get("refused"):
        flags.append('the reader answered "Not in ..." (a likely MISS)')
    if rec.get("citation_retry"):
        flags.append("its first answer cited ids that are not units (" + ", ".join(rec["citation_retry"])
                     + ") and it was asked again")
    if rec.get("retry_outcome"):
        flags.append(f"the retry ended with {rec['retry_outcome']}, so the first answer was kept")
    if rec.get("unknown_units"):
        flags.append("the answer cites ids that are not units: " + ", ".join(rec["unknown_units"]))
    if rec.get("malformed_tags"):
        flags.append(f"{len(rec['malformed_tags'])} malformed source tag(s) in the answer")
    if rec.get("outcome") and rec.get("outcome") != "success":
        flags.append(f"the run ended with {rec['outcome']}")
    lines = [f"# Transcript {path.stem}", "", f"mode: {rec.get('mode')}", f"time: {rec.get('time')}",
             f"turns: {rec.get('turns')}", "flags: " + ("; ".join(flags) if flags else "none"), "",
             "## Question", "", rec.get("question", ""), ""]
    if rec.get("first_stream"):
        lines += ["## First attempt (rejected by the program for its citations)", ""]
        lines += reader_steps(Path(rec["first_stream"]))
        lines += ["", "First answer:", "", rec.get("first_answer") or "(none)", ""]
    lines += ["## What the reader did", ""]
    lines += reader_steps(Path(rec.get("stream") or ""))
    lines += ["", "## Answer", "", rec.get("answer") or "(none)", ""]
    return "\n".join(lines)


SOURCE_LAYER = ("wiki/notes/", "wiki/addenda/", "wiki/contracts/", "wiki/relations/", "wiki/metrics/")


def added_source_lines(diff: str) -> list[str]:
    """Lines a diff adds to the per-unit source layer (entity pages repeat them, so they are not counted)."""
    out, current = [], None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            current = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("+") and current and current.startswith(SOURCE_LAYER):
            out.append(ln[1:])
    return out


def dreams_path(ws) -> Path:
    return ws.runs / "dreams.json"


def load_dreams(ws) -> list[dict]:
    p = dreams_path(ws)
    return json.loads(p.read_text()) if p.exists() else []


def save_dreams(ws, dreams: list[dict]) -> None:
    dreams_path(ws).write_text(json.dumps(dreams, indent=1))


def open_dream(ws) -> dict | None:
    """The dream whose branch is still there: one under review, one that failed, or one that was cut short (its
    status stayed "running"; whoever holds the workspace lock knows no dream is running now)."""
    for d in reversed(load_dreams(ws)):
        if d["status"] in ("review", "failed-check", "running"):
            return d
    return None


def dream(ws, estimate_only=False, max_usd=None, model=None, concurrency=None, approve_over_cap=False) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    pending = open_dream(ws)
    if pending and pending["status"] == "review":
        raise WikiError(f"Dream branch {pending['branch']} is open for review: wiki dream --accept or --discard first.")
    if pending:
        raise WikiError(f"Dream branch {pending['branch']} did not finish: wiki dream --discard first.")
    transcripts = collect_transcripts(ws)
    need = int(cfg.runner("dream_min_transcripts", 5))
    group = int(cfg.runner("dream_group", 10))
    models = {r: model or cfg.model(r) for r in ("dream_analyst", "dream_apply", "link", "compile", "index")}
    averages = ledger.averages()
    est = cost_mod.estimate_dream(len(transcripts), averages, models, group)
    cap_limit = min(cfg.cap("dream_usd"), max_usd) if max_usd else cfg.cap("dream_usd")
    print(f"{len(transcripts)} ask transcript(s) since the last accepted dream; estimate {cost_mod.usd(est)} "
          f"(cap {cost_mod.usd(cap_limit)})")
    if len(transcripts) < need:
        print(f"Fewer than {need} transcripts: nothing to dream about yet.")
        return 0
    if estimate_only:
        return 0
    if not gitops.is_clean(ws.repo) or gitops.current_branch(ws.repo) != "main":
        raise WikiError("repo/ must be on main with no uncommitted changes before a dream.")
    runner = Runner(ws, cfg, ledger, concurrency)
    runner.check_ready()
    reserve = float(cfg.runner("reserve", 0.10))
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, est, cap_limit, approve_over_cap, "this dream", reserve)

    ts = new_id(lambda ts: (ws.runs / f"dream-{ts}").exists() or gitops.ref_exists(ws.repo, f"dream/{ts}"))
    run_id = f"dream-{ts}"
    branch = f"dream/{ts}"
    base = gitops.head(ws.repo)
    wt = ws.runs / run_id / "branch"
    private_dir(wt.parent)
    gitops.worktree_add(ws.repo, wt, base, branch=branch)
    groups = [transcripts[i:i + group] for i in range(0, len(transcripts), group)]
    # shares: analysts, apply, link, compile and index, each in proportion to what such a run has cost here
    est_tasks = cost_mod.dream_task_estimates([len(g) for g in groups], averages, models)
    shares = cost_mod.share(est_tasks, cap, reserve)
    ctx = Context(ws, "dream", run_id, wt, cfg=cfg, runner=runner, model_override=model, shares=shares, cap=cap)
    record = {"ts": ts, "branch": branch, "base": base, "worktree": str(wt), "status": "running",
              "transcripts": [p.name for p in transcripts]}
    save_dreams(ws, load_dreams(ws) + [record])

    def update(**fields):
        dreams = load_dreams(ws)
        for d in dreams:
            if d["ts"] == ts:
                d.update(fields)
        save_dreams(ws, dreams)

    try:
        # 3. analysts, read-only, in parallel
        prompt = write_prompt(ctx.run_dir, "dream_analyst", render("dream_analyst", steering=cfg.steering_text()))
        findings_dir = ctx.run_dir / "findings"
        findings_dir.mkdir(exist_ok=True)

        def analyst(item):
            n, paths = item
            folder = input_folder(ctx, f"transcripts-{n:02d}", {f"{p.stem}.md": transcript_markdown(p) for p in paths})
            spec = RunSpec(
                command="dream", phase="dream-analyst", role="dream_analyst", task_id=f"analyst-{n:02d}",
                run_dir=ctx.run_dir, cwd=ws.wiki,
                task=f"Dream analyst: review the transcripts in {folder}. Source documents: {ws.docs}.",
                system_prompt_file=prompt, model=model or cfg.model("dream_analyst"),
                budget_usd=shares[f"analyst-{n:02d}"], turns=cfg.turns("dream_analyst"),
                timeout_s=cfg.timeout("dream_analyst"), add_dirs=[folder, ws.docs],
            )
            rr = runner.run(spec)
            text = rr.text if rr.ok else ""
            (findings_dir / f"findings-{n:02d}.md").write_text(text + "\n")
            return rr

        analysts = run_all(runner, analyst, list(enumerate(groups, 1)), ctx.concurrency)
        lost = [f"{a.task_id} ({a.outcome})" for a in analysts if not a.ok]
        if lost:  # read as "no findings", the dream passed, and accepting it used up transcripts nobody had read
            raise WikiError(f"analyst run(s) failed: {', '.join(lost)}; their transcripts were not read")
        findings = [ln for p in sorted(findings_dir.glob("*.md")) for ln in p.read_text().splitlines()
                    if ln.strip().startswith("- ") and ln.strip() != "- NONE"]
        make_read_only(findings_dir)
        print(f"Analysts: {sum(1 for a in analysts if a.ok)}/{len(analysts)} returned; {len(findings)} finding(s)")
        if findings:
            # 4. one apply run, alone
            def validate(wiki, changed):
                return survival_problems(wiki, changed) + lint_changed(ctx.manifest(), wiki, changed)

            apply = WriterTask(
                task_id="apply", phase="dream-apply", role="dream_apply", prompt="dream_apply",
                task_text=f"Dream apply: confirm and file the findings in {findings_dir}. Source documents: {ws.docs}. "
                          "Read _schema.md first.",
                owned_files={"_aliases.md"}, owned_dirs=("addenda", "open_questions"),
                lifecycle_dirs=("notes", "contracts", "relations", "metrics"),
                add_dirs=[findings_dir, ws.docs], validate=validate,
            )
            run_parallel(ctx, [apply])
            if ctx.task_state("apply").get("status") != "done":
                raise WikiError(f"apply failed: {ctx.task_state('apply').get('reason')}")
            ctx.commit(f"dream {ts}: apply findings")
            # 5. recompile touched entities, rerun the index
            t = link_task(ctx)
            if t:
                run_parallel(ctx, [t])
                if ctx.task_state("link").get("status") != "done":
                    raise WikiError(f"link failed: {ctx.task_state('link').get('reason')}")
            failed = phase_compile(ctx)
            if failed:
                raise WikiError(f"compile failed: {', '.join(failed)}")
            run_parallel(ctx, [index_task(ctx)])
            if ctx.task_state("index").get("status") != "done":
                raise WikiError(f"index failed: {ctx.task_state('index').get('reason')}")
            split_escalations(ctx.wiki)  # as a build does: a long escalations.md would fail the check below on its size
            ctx.commit(f"dream {ts}: recompile and reindex")
        # 6. the invariants
        m = ctx.manifest()
        findings_check = check_mod.run_checks(wt / "wiki", m, repo=wt, against=base)
        errs = check_mod.errors(findings_check)
        (ctx.run_dir / "check.txt").write_text(check_mod.report(findings_check, show=10_000) + "\n")
        changed = gitops.diff_names(ws.repo, base, branch)
        diff = gitops.diff_text(ws.repo, base, branch)
        added = added_source_lines(diff)
        conflicts = sum(1 for ln in added if "CONFLICT" in ln)
        retracted = sum(1 for ln in added if "retracted:" in ln)
        summary = {"findings": len(findings), "files_changed": changed, "conflicts_marked": conflicts,
                   "lines_retracted": retracted, "check_errors": len(errs), "spent": round(ctx.spent_this_run(), 6)}
        update(status="review" if not errs else "failed-check", summary=summary)
    except WikiError as why:
        update(status="failed-check", error=str(why), spent=round(ctx.spent_this_run(), 6))
        print(f"Dream {ts} failed: {why}. Inspect branch {branch}, then wiki dream --discard.")
        return 2
    except BaseException as why:  # Ctrl-C, or an error the program did not foresee: the branch can be discarded
        update(status="failed-check", error=f"stopped before it finished ({type(why).__name__})",
               spent=round(ctx.spent_this_run(), 6))
        raise
    print(f"Dream {ts} on branch {branch}: {len(findings)} finding(s), {len(changed)} file(s) changed, "
          f"{conflicts} conflict(s) marked, {retracted} line(s) retracted; spent {cost_mod.usd(summary['spent'])}.")
    for p in changed:
        print(f"  {p}")
    if errs:
        print(check_mod.report(findings_check, show=30))
        print(f"wiki check --against {base[:10]} failed: this dream cannot be accepted. Run wiki dream --discard.")
        return 2
    print(f"wiki check --against {base[:10]} passed. Review with: git -C {ws.repo} diff {base[:10]}..{branch}")
    print("Then wiki dream --accept to merge it, or wiki dream --discard to delete it.")
    return 0


def accept(ws) -> int:
    d = open_dream(ws)
    if not d:
        raise WikiError("No dream branch is open for review.")
    if d["status"] != "review":
        raise WikiError(f"Dream {d['branch']} did not finish, or did not pass wiki check --against its base; it can "
                        "only be discarded (wiki dream --discard).")
    if not gitops.is_clean(ws.repo) or gitops.current_branch(ws.repo) != "main":
        raise WikiError("repo/ must be on main with no uncommitted changes to accept a dream.")
    # re-check on the branch tip in case it was edited after the dream
    m_text = gitops.show(ws.repo, d["branch"], "manifest.json")
    from .manifest import Manifest

    wt = Path(d["worktree"])
    # the check below reads the worktree's files and the merge takes the branch tip: they must be the same thing
    if m_text is None or not wt.is_dir() or gitops.head(wt) != gitops.git(ws.repo, "rev-parse", d["branch"]).strip() \
            or gitops.changed_paths(wt):
        raise WikiError(f"{wt} is missing, is not at the tip of {d['branch']}, or has changes that are not "
                        "committed. Commit them on that branch or undo them, then accept again (or wiki dream "
                        "--discard).")
    findings = check_mod.run_checks(wt / "wiki", Manifest(json.loads(m_text)), repo=ws.repo, against=d["base"])
    if check_mod.errors(findings):
        print(check_mod.report(findings, show=30))
        raise WikiError("The branch no longer passes wiki check --against its base.")
    try:
        gitops.git(ws.repo, "merge", "--no-ff", "-q", "-m", f"Accept dream {d['ts']}", d["branch"])
    except WikiError as why:  # main moved since the dream started and both changed the same lines
        gitops.git(ws.repo, "merge", "--abort", check=False)  # main, and the branch under review, stay as they were
        raise WikiError(f"{d['branch']} does not merge into main, which has changed since the dream started; main "
                        f"is as it was. Discard the dream (wiki dream --discard) and dream again. git said: "
                        f"{str(why).splitlines()[-1]}") from None
    gitops.git(ws.repo, "tag", f"{TAG_PREFIX}{d['ts']}")
    gitops.worktree_remove(ws.repo, wt)
    gitops.git(ws.repo, "branch", "-d", d["branch"])
    dreams = load_dreams(ws)
    for x in dreams:
        if x["ts"] == d["ts"]:
            x["status"] = "accepted"
            x["accepted_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    save_dreams(ws, dreams)
    print(f"Merged {d['branch']} into main and tagged {TAG_PREFIX}{d['ts']}.")
    return 0


def discard(ws) -> int:
    d = open_dream(ws)
    if not d:
        raise WikiError("No dream branch is open.")
    gitops.worktree_remove(ws.repo, Path(d["worktree"]))
    gitops.git(ws.repo, "branch", "-D", d["branch"], check=False)
    remove_tree(Path(d["worktree"]))
    dreams = load_dreams(ws)
    for x in dreams:
        if x["ts"] == d["ts"]:
            x["status"] = "discarded"
    save_dreams(ws, dreams)
    print(f"Deleted {d['branch']}.")
    return 0
