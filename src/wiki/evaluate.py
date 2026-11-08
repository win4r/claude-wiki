"""wiki eval: ask every question in every mode, grade each answer against its criteria, write a report."""

from __future__ import annotations

import json
import statistics
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from . import WikiError
from . import cost as cost_mod
from .ask import MODES, ask_once
from .ledger import Ledger
from .prompts import render, write_prompt
from .runner import Runner, RunSpec
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
        if q["kind"] not in KINDS:
            raise WikiError(f"{path}: question {q['id']} has kind {q['kind']!r}; use one of {sorted(KINDS)}")
        if q["id"] in seen:
            raise WikiError(f"{path}: duplicate id {q['id']}")
        if not q["criteria"]:
            raise WikiError(f"{path}: question {q['id']} has no criteria")
        seen.add(q["id"])
    if not qs:
        raise WikiError(f"{path}: no [[question]] entries")
    return qs


def parse_grade(text: str, n: int) -> list[bool] | None:
    """The grader's {"met": [...]} with exactly n booleans, or None."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    met = obj.get("met") if isinstance(obj, dict) else None
    if not isinstance(met, list) or len(met) != n or not all(isinstance(x, bool) for x in met):
        return None
    return met


def score(records: list[dict], build_cost: float) -> dict:
    """Per-mode score, cost per answer, trap refusals, unknown citations, and the break-even point."""
    out = {"modes": {}, "build_cost_usd": build_cost}
    for mode in MODES:
        rs = [r for r in records if r["mode"] == mode]
        if not rs:
            continue
        met = sum(sum(r["met"]) for r in rs if r.get("met") is not None)
        possible = sum(len(r["criteria"]) for r in rs)
        costs = [r["cost_usd"] for r in rs]
        traps = [r for r in rs if r["kind"] == "trap"]
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
    lines = [f"# Eval report: {name}", "", f"Build cost (ledger): {cost_mod.usd(summary['build_cost_usd'])}", "",
             "| mode | score | criteria met | median cost | mean cost | traps refused | break-even answers |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, m in summary["modes"].items():
        be = m.get("break_even_answers")
        lines.append(
            f"| {mode} | {m['score']:.0%} | {m['criteria_met']}/{m['criteria_total']} | "
            f"{cost_mod.usd_fine(m['median_cost_usd'])} | {cost_mod.usd_fine(m['mean_cost_usd'])} | "
            f"{m['traps_refused']}/{m['traps']} | {'-' if mode == 'sources' else (f'{be:,.0f}' if be else 'never')} |")
    lines.append("")
    for mode, m in summary["modes"].items():
        if m["unknown_citations"]:
            lines.append(f"- {mode}: cited units not in the manifest: {', '.join(m['unknown_citations'])}")
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
    runner = Runner(ws, cfg, ledger, concurrency)
    runner.check_ready()
    reserve = float(cfg.runner("reserve", 0.10))
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, total_est, cap_limit, approve_over_cap, "this eval",
                             reserve)
    shares = cost_mod.share(estimates, cap, reserve)
    eval_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = private_dir(ws.runs / "eval" / eval_id)  # never feeds dreaming
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

    with ThreadPoolExecutor(max_workers=max(1, concurrency or int(cfg.runner("concurrency", 4)))) as pool:
        records = list(pool.map(one, jobs))
    build_cost = ledger.spent_where(command="build")
    summary = score(records, build_cost)
    summary["file"] = path.name
    summary["eval_id"] = eval_id
    summary["spent_usd"] = sum(r["cost_usd"] + r.get("grade_cost_usd", 0) for r in records)
    ws.eval_dir.mkdir(exist_ok=True)
    stem = f"report-{path.stem}-{eval_id}"
    (ws.eval_dir / f"{stem}.json").write_text(json.dumps({"summary": summary, "answers": records}, indent=1,
                                                         ensure_ascii=False))
    (ws.eval_dir / f"{stem}.md").write_text(report_markdown(path.name, summary, records))
    from . import gitops

    gitops.commit_all(ws.repo, f"eval {eval_id}: report for {path.name}")
    print(report_markdown(path.name, summary, records).split("## Answers")[0])
    print(f"Report: {ws.eval_dir / (stem + '.md')}; spent {cost_mod.usd(summary['spent_usd'])}")
    return 0
