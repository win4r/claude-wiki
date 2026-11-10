"""wiki eval: ask every question in every mode, grade each answer against its criteria, write a report."""

from __future__ import annotations

import json
import re
import statistics
import tempfile
import tomllib
from pathlib import Path

from . import WikiError
from . import cost as cost_mod
from .ask import MODES, ask_once
from .ledger import Ledger
from .prompts import render, write_prompt
from .runner import Runner, RunSpec, run_all
from .workspace import private_dir

KINDS = {"lookup", "cross-document", "trap"}


def load_questions(path: Path) -> list[dict]:
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as why:
        raise WikiError(f"{path}: {why}") from None
    qs = data.get("question") or []
    seen = set()
    for q in qs:
        for key in ("id", "kind", "question", "criteria"):
            if key not in q:
                raise WikiError(f"{path}: question {q.get('id', '?')} has no {key}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(q["id"])):  # an id names its runs' files
            raise WikiError(f"{path}: question id {q['id']!r} may hold only letters, digits, '.', '_' and '-'")
        if q["kind"] not in KINDS:
            raise WikiError(f"{path}: question {q['id']} has kind {q['kind']!r}; use one of {sorted(KINDS)}")
        if q["id"] in seen:
            raise WikiError(f"{path}: duplicate id {q['id']}")
        if not q["criteria"]:
            raise WikiError(f"{path}: question {q['id']} has no criteria")
        if not isinstance(q["criteria"], list) or not all(isinstance(c, str) and c.strip() for c in q["criteria"]):
            raise WikiError(f"{path}: question {q['id']}: criteria must be a list of sentences, one per point")
        seen.add(q["id"])
    if not qs:
        raise WikiError(f"{path}: no [[question]] entries")
    return qs


def parse_grade(text: str, n: int) -> list[bool] | None:
    """The grader's last {"met": [...]} with exactly n booleans, or None.

    A grader sometimes corrects itself and prints a second object after the first, so every object in the
    text is tried and the last valid one wins."""
    text = re.sub(r",(\s*[\]}])", r"\1", text or "")  # a comma before a closing bracket is not JSON; graders write it
    decoder = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        met = obj.get("met") if isinstance(obj, dict) else None
        if isinstance(met, list) and len(met) == n and all(isinstance(x, bool) for x in met):
            found = met
        i = text.find("{", end)
    return found


def _criteria(rs: list[dict]) -> tuple[int, int]:
    """(criteria met, criteria possible); an ungraded answer meets none of its criteria."""
    return (sum(sum(r["met"]) for r in rs if r.get("met") is not None), sum(len(r["criteria"]) for r in rs))


def answer_tokens(r: dict) -> int | None:
    """Every token one answer took (input, output, cache reads and writes, over all its runs), when recorded."""
    usage = r.get("usage")
    return sum(int(v or 0) for v in usage.values()) if usage else None


def score(records: list[dict], build_cost: float) -> dict:
    """Per-mode score (also per question kind), cost and tokens per answer, trap refusals, unknown citations,
    and the break-even point."""
    out = {"modes": {}, "build_cost_usd": build_cost}
    for mode in MODES:
        rs = [r for r in records if r["mode"] == mode]
        if not rs:
            continue
        met, possible = _criteria(rs)
        costs = [r["cost_usd"] for r in rs]
        traps = [r for r in rs if r["kind"] == "trap"]
        by_kind = {}
        for kind in sorted({r["kind"] for r in rs}):
            k_met, k_possible = _criteria([r for r in rs if r["kind"] == kind])
            by_kind[kind] = {"answers": sum(1 for r in rs if r["kind"] == kind), "criteria_met": k_met,
                             "criteria_total": k_possible, "score": k_met / k_possible if k_possible else 0.0}
        tokens = [t for t in (answer_tokens(r) for r in rs) if t is not None]
        out["modes"][mode] = {
            "answers": len(rs),
            "criteria_met": met,
            "criteria_total": possible,
            "score": met / possible if possible else 0.0,
            "median_cost_usd": statistics.median(costs),
            "mean_cost_usd": statistics.fmean(costs),
            "traps_refused": sum(1 for r in traps if r["refused"]),
            "traps": len(traps),
            "ungraded": sum(1 for r in rs if r.get("met") is None),
            "unknown_citations": sorted({u for r in rs for u in r["unknown_units"]}),
            "citation_retries": sum(1 for r in rs if r.get("citation_retry")),
            "by_kind": by_kind,
            "median_tokens": statistics.median(tokens) if tokens else None,
        }
    src = out["modes"].get("sources")
    for mode, m in out["modes"].items():
        if mode == "sources" or not src:
            continue
        saving = src["mean_cost_usd"] - m["mean_cost_usd"]
        m["saving_per_answer_usd"] = saving
        m["break_even_answers"] = (build_cost / saving) if saving > 0 else None
    return out


def report_markdown(name: str, summary: dict, records: list[dict]) -> str:
    commit = f"; wiki at commit {summary['wiki_commit'][:10]}" if summary.get("wiki_commit") else ""
    lines = [f"# Eval report: {name}", "", f"Build cost (ledger): {cost_mod.usd(summary['build_cost_usd'])}{commit}", "",
             "| mode | score | criteria met | median cost | mean cost | traps refused | break-even answers |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, m in summary["modes"].items():
        be = m.get("break_even_answers")
        lines.append(
            f"| {mode} | {m['score']:.0%} | {m['criteria_met']}/{m['criteria_total']} | "
            f"{cost_mod.usd_fine(m['median_cost_usd'])} | {cost_mod.usd_fine(m['mean_cost_usd'])} | "
            f"{m['traps_refused']}/{m['traps']} | "
            + ("-" if "break_even_answers" not in m else f"{be:,.0f}" if be is not None else "never") + " |")
    lines.append("")
    kinds = sorted({k for m in summary["modes"].values() for k in m.get("by_kind", {})})
    if kinds:
        lines += ["By question kind (criteria met), and tokens per answer:", "",
                  "| mode | " + " | ".join(kinds) + " | median tokens |",
                  "| --- | " + " | ".join("---" for _ in kinds) + " | --- |"]
        for mode, m in summary["modes"].items():
            cells = []
            for k in kinds:
                b = m.get("by_kind", {}).get(k)
                cells.append(f"{b['score']:.0%} ({b['criteria_met']}/{b['criteria_total']})" if b else "-")
            tok = m.get("median_tokens")
            lines.append(f"| {mode} | " + " | ".join(cells) + f" | {f'{tok:,.0f}' if tok is not None else '-'} |")
        lines.append("")
    for mode, m in summary["modes"].items():
        if m["unknown_citations"]:
            lines.append(f"- {mode}: cited units not in the manifest: {', '.join(m['unknown_citations'])}")
        if m.get("citation_retries"):
            lines.append(f"- {mode}: {m['citation_retries']} answer(s) asked again for citing ids outside the manifest "
                         "(both runs' cost counted)")
        if m["ungraded"]:
            lines.append(f"- {mode}: {m['ungraded']} answer(s) could not be graded")
        if mode != "sources" and m.get("saving_per_answer_usd") is not None and m["saving_per_answer_usd"] <= 0:
            lines.append(f"- {mode}: break-even never: its answers cost {cost_mod.usd_fine(-m['saving_per_answer_usd'])} "
                         "more on average than sources-mode answers, so the build cost is never paid back")
    lines += ["", "## Answers", ""]
    for r in records:
        met = r.get("met")
        lines.append(f"### {r['id']} ({r['kind']}) - {r['mode']}: "
                     f"{'ungraded' if met is None else f'{sum(met)}/{len(met)}'} - {cost_mod.usd_fine(r['cost_usd'])}")
        lines.append("")
        lines.append("> " + (r["answer"] or "(no answer)").replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines) + "\n"


def grade_once(ws, cfg, runner: Runner, run_dir: Path, rec: dict, budget: float, model: str) -> list[bool] | None:
    prompt_file = write_prompt(run_dir, "grader", render("grader"))
    with tempfile.TemporaryDirectory(prefix="wiki-grade-") as tmp:
        tmp = Path(tmp)
        (tmp / "answer.md").write_text(f"# Question\n\n{rec['question']}\n\n# Answer\n\n{rec['answer'] or '(none)'}\n")
        (tmp / "criteria.md").write_text(
            "\n".join(f"{i}. {c}" for i, c in enumerate(rec["criteria"], 1)) + "\n")
        spec = RunSpec(
            command="eval", phase="grade", role="grader", task_id=f"grade-{rec['id']}-{rec['mode']}",
            run_dir=run_dir, cwd=tmp, task="Grade: read answer.md and criteria.md here and grade the answer.",
            system_prompt_file=prompt_file, model=model, budget_usd=budget, turns=cfg.turns("grader"),
            timeout_s=cfg.timeout("grader"),
        )
        rr = runner.run(spec)
    rec["grade_cost_usd"] = rr.cost_usd
    return parse_grade(rr.text, len(rec["criteria"])) if rr.ok else None


def run_eval(ws, file: Path, estimate_only: bool = False, max_usd: float | None = None, model: str | None = None,
             concurrency: int | None = None, approve_over_cap: bool = False, modes=MODES) -> int:
    modes = tuple(modes)  # refused before the estimate: a mistyped or repeated mode used to be paid for, then fail
    if not modes or len(set(modes)) != len(modes) or any(m not in MODES for m in modes):
        raise WikiError(f"--modes takes one or more of {', '.join(MODES)}, each once; got {','.join(modes) or 'none'}.")
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    path = Path(file)
    if not path.exists() and (ws.eval_dir / path.name).exists():
        path = ws.eval_dir / path.name
    questions = load_questions(path)
    ask_model = model or cfg.model("ask")
    grade_model = cfg.model("grader")
    averages = ledger.averages()
    est_q = {m: cost_mod.estimate_question(averages, m, ask_model) for m in modes}
    est_g = cost_mod.estimate_grade(averages, grade_model)
    estimates = {}
    for q in questions:
        for m in modes:
            estimates[f"{q['id']}-{m}"] = est_q[m]
            estimates[f"grade-{q['id']}-{m}"] = est_g
    total_est = sum(estimates.values())
    cap_limit = min(cfg.cap("eval_usd"), max_usd) if max_usd else cfg.cap("eval_usd")
    print(f"Estimate for wiki eval: {len(questions)} questions x {len(modes)} modes, plus grading: "
          f"{cost_mod.usd(total_est)} (cap {cost_mod.usd(cap_limit)})")
    if estimate_only:
        return 0
    from . import gitops
    from .phases import latest_unfinished, new_id

    reads_wiki = any(m in ("wiki", "both") for m in modes)
    if reads_wiki:  # the report names the commit its answers read, so that commit must be the wiki they read
        dirty = gitops.git(ws.repo, "status", "--porcelain", "--untracked-files=all", "--", "wiki", "manifest.json")
        if dirty.strip():
            raise WikiError("repo/wiki or manifest.json has changes that are not committed, so the report could not "
                            "say which wiki its answers read. Commit them, or finish the build with wiki build --resume.")
        unfinished = latest_unfinished(ws)
        if unfinished is not None:
            raise WikiError(f"A build is unfinished ({unfinished.parent.name}); finish it with wiki build --resume "
                            "before an eval of the wiki.")
    unfinished_git = gitops.unfinished_operation(ws.repo)
    if unfinished_git:  # no commit can be made until it ends: every run would be paid for, then the report not committed
        raise WikiError(f"repo/ is in the middle of {unfinished_git}, so the report could not be committed. Finish "
                        "it or abort it first (for a merge: git -C repo merge --abort).")
    eval_id = new_id(lambda ts: (ws.runs / "eval" / ts).exists())
    stem = f"report-{path.stem}-{eval_id}"
    report_paths = [f"eval/{stem}.json", f"eval/{stem}.md"]
    if path.resolve().is_relative_to(ws.repo.resolve()):
        report_paths.append(str(path.resolve().relative_to(ws.repo.resolve())))  # a question file kept in eval/
    # the report is committed with its question file: refuse before any run, not after every run is paid for
    blocked = gitops.ignored(ws.repo, report_paths)
    if blocked:
        raise WikiError(f"git ignores {', '.join(blocked)} (a .gitignore rule or an excludes file), so the report "
                        "could not be committed with its questions. Remove the rule, or keep the question file "
                        "outside repo/.")
    runner = Runner(ws, cfg, ledger, concurrency)
    runner.check_ready()
    reserve = float(cfg.runner("reserve", 0.10))
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, total_est, cap_limit, approve_over_cap, "this eval",
                             reserve)
    shares = cost_mod.share(estimates, cap, reserve)
    run_dir = private_dir(ws.runs / "eval" / eval_id)  # never feeds dreaming
    # the wiki every answer reads; the workspace lock keeps it still. A sources-only eval reads no wiki.
    wiki_commit = gitops.head(ws.repo) if reads_wiki else None
    jobs = [(q, m) for q in questions for m in modes]

    def one(job):
        q, m = job
        rec = ask_once(ws, cfg, runner, q["question"], m, run_dir, f"{q['id']}-{m}", shares[f"{q['id']}-{m}"],
                       ask_model, command="eval")
        rec.update({"id": q["id"], "kind": q["kind"], "criteria": list(q["criteria"])})
        rec["met"] = grade_once(ws, cfg, runner, run_dir, rec, shares[f"grade-{q['id']}-{m}"], grade_model)
        met = rec["met"]
        grade = "ungraded" if met is None else f"{sum(met)}/{len(met)}"
        print(f"  {q['id']} {m}: {grade} {cost_mod.usd_fine(rec['cost_usd'])}", flush=True)
        return rec

    records = run_all(runner, one, jobs, concurrency or int(cfg.runner("concurrency", 4)))
    build_cost = ledger.spent_where(command="build")
    summary = score(records, build_cost)
    summary["file"] = path.name
    summary["eval_id"] = eval_id
    summary["wiki_commit"] = wiki_commit
    summary["spent_usd"] = sum(r["cost_usd"] + r.get("grade_cost_usd", 0) for r in records)
    ws.eval_dir.mkdir(exist_ok=True)
    (ws.eval_dir / f"{stem}.json").write_text(json.dumps({"summary": summary, "answers": records}, indent=1,
                                                         ensure_ascii=False))
    (ws.eval_dir / f"{stem}.md").write_text(report_markdown(path.name, summary, records))
    gitops.commit_paths(ws.repo, report_paths, f"eval {eval_id}: report for {path.name}")
    print(report_markdown(path.name, summary, records).split("## Answers")[0])
    print(f"Report: {ws.eval_dir / (stem + '.md')}; spent {cost_mod.usd(summary['spent_usd'])}")
    return 0
