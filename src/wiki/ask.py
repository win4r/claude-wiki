"""wiki ask: one fresh read-only session per question, in wiki, sources or both mode."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from . import WikiError
from . import cost as cost_mod
from .grammar import tags_in
from .ledger import Ledger
from .manifest import Manifest
from .prompts import render, write_prompt
from .runner import Runner, RunSpec
from .workspace import private_dir

MODES = ("wiki", "sources", "both")
REFUSALS = ("not in the wiki", "not in the documents", "not in the data room")
LOOSE_SOURCE = re.compile(r"source:\s*([A-Za-z0-9_.-]+)")
TAG_FIRST_ID = re.compile(r"\[source:\s*([^|\]\n;,]+)", re.I)  # in a malformed tag, what stands where the unit goes
TAG_NEXT_ID = re.compile(r";\s*(?:source:\s*)?([A-Za-z0-9_.-]+)\s*(?=\||\]|$)")  # "; <id> |" and "; <id>]"


def refused(answer: str) -> bool:
    head = re.sub(r"^[\s*_#>`\"']+", "", answer or "").lower()
    return head.startswith(REFUSALS)


def cited_units(answer: str) -> tuple[list[str], list[str]]:
    """(units cited in valid tags, malformed tag snippets)."""
    units, bad = [], []
    for line in (answer or "").splitlines():
        good, malformed = tags_in(line)
        units += [t["unit"] for t in good]
        bad += malformed
    loose = LOOSE_SOURCE.findall(answer or "")
    units += [u for u in loose if u not in units and u.lower() == u]
    for snippet in bad:  # a malformed tag's ids too, whatever their case, and those after the first
        first = TAG_FIRST_ID.match(snippet)
        units += ([first.group(1).strip()] if first and first.group(1).strip() else []) + TAG_NEXT_ID.findall(snippet)
    return sorted(set(units)), bad


def spec_for(ws, cfg, mode: str, question: str, run_dir: Path, task_id: str, budget: float,
             model: str, command: str) -> RunSpec:
    if mode not in MODES:
        raise WikiError(f"--mode must be one of {', '.join(MODES)}")
    prompt = {"wiki": "ask_wiki", "sources": "ask_sources", "both": "ask_both"}[mode]
    prompt_file = write_prompt(run_dir, prompt, render(prompt, steering=cfg.steering_text()))
    if mode == "wiki":
        cwd, add, text = ws.wiki, [], f"Ask: {question}"
    elif mode == "sources":
        cwd, add, text = ws.docs, [], f"Ask: {question}"
    else:
        cwd, add, text = ws.wiki, [ws.docs], f"Ask: {question}\n(Source documents, read-only: {ws.docs})"
    return RunSpec(
        command=command, phase=f"ask-{mode}", role="ask", task_id=task_id, run_dir=run_dir, cwd=cwd,
        task=text, system_prompt_file=prompt_file, model=model, budget_usd=budget,
        turns=cfg.turns("ask"), timeout_s=cfg.timeout("ask"), add_dirs=add,
    )


SOURCES_ROW = re.compile(r"^\|\s*([a-z0-9][a-z0-9_.-]*)\s*\|\s*[a-z_]+/", re.M)
CITATION_NOTE = ("Your first answer cited ids that are not source units, so it was rejected. Answer again, citing in "
                 "every tag one unit id from the manifest (the stem of a file under notes/, or of a source document) "
                 "and nothing after it:")


def units_of_page(page: Path) -> list[str]:
    """The unit ids in an entity page's `## Sources` table."""
    try:
        return sorted(set(SOURCES_ROW.findall(page.read_text())))
    except OSError:
        return []


def citation_feedback(ws, unknown: list[str]) -> str:
    """One line per id that is not a unit, saying what to cite instead."""
    lines = [CITATION_NOTE]
    for u in unknown:
        page = next((p for p in (ws.wiki / "entities" / f"{u}.md", ws.wiki / f"{u}.md") if p.is_file()), None)
        if u.startswith("_") or u.endswith(".md"):
            lines.append(f"- `{u}` is a wiki file, not a source unit: drop the fact or cite the unit in its tag.")
        elif page is not None and units_of_page(page):
            lines.append(f"- `{u}` is an entity page; its facts come from the units "
                         f"{', '.join(units_of_page(page))}: cite the unit in the tag of the fact line you read.")
        else:
            lines.append(f"- `{u}` is not a unit id (not an exhibit or agreement name either): cite the unit in the "
                         "tag of the line you read, or drop the fact.")
    lines.append("- Never write \"<a> via <b>\" in a tag: cite <b> only.")
    return "\n".join(lines)


def _run_and_read(ws, runner: Runner, spec: RunSpec):
    rr = runner.run(spec)
    manifest = Manifest.load(ws.manifest_path)
    units, malformed = cited_units(rr.text)
    return rr, units, [u for u in units if u not in manifest.units], malformed


def ask_once(ws, cfg, runner: Runner, question: str, mode: str, run_dir: Path, task_id: str, budget: float,
             model: str, command: str = "ask") -> dict:
    """One answer. An answer that cites ids outside the manifest is asked again once, told which ids were wrong."""
    spec = spec_for(ws, cfg, mode, question, run_dir, task_id, budget, model, command)
    rr, units, unknown, malformed = _run_and_read(ws, runner, spec)
    cost, turns, first_unknown = rr.cost_usd, rr.turns, []
    usage = dict(rr.usage or {})
    first = failed = None
    left = budget - rr.cost_usd
    if unknown and rr.ok and left >= float(cfg.runner("min_retry_usd", 0.01)):
        retry = dataclasses.replace(spec, task_id=f"{task_id}-cite", budget_usd=left,
                                    task=spec.task + "\n\n" + citation_feedback(ws, unknown))
        rr2, units2, unknown2, malformed2 = _run_and_read(ws, runner, retry)
        cost, turns = cost + rr2.cost_usd, (turns or 0) + (rr2.turns or 0)
        for key, n in (rr2.usage or {}).items():
            usage[key] = usage.get(key, 0) + n
        first_unknown = unknown  # it was asked again, whatever came of it
        if rr2.ok and rr2.text:
            first = rr
            rr, units, unknown, malformed = rr2, units2, unknown2, malformed2
        else:
            failed = rr2
    record = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command, "question": question, "mode": mode, "model": model, "answer": rr.text,
        "outcome": rr.outcome, "cost_usd": cost, "session_id": rr.session_id, "turns": turns,
        "usage": usage,  # tokens of every run this answer took
        "cited_units": units, "unknown_units": unknown, "malformed_tags": malformed,
        "citation_retry": first_unknown,
        "refused": refused(rr.text), "stream": str(rr.stream_path),
    }
    if first is not None:  # the rejected first attempt, kept so a dream sees what that reader read
        record["first_answer"], record["first_stream"] = first.text, str(first.stream_path)
    if failed is not None:  # the retry gave no answer, so the first one stands
        record["retry_outcome"] = failed.outcome if not failed.ok else "an empty answer"
        record["retry_stream"] = str(failed.stream_path)
    (Path(run_dir) / f"{task_id}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return record


def ask(ws, question: str, mode: str = "both", estimate_only: bool = False, max_usd: float | None = None,
        model: str | None = None, approve_over_cap: bool = False) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    model = model or cfg.model("ask")
    est = cost_mod.estimate_question(ledger.averages(), mode, model)
    cap_limit = min(cfg.cap("question_usd"), max_usd) if max_usd else cfg.cap("question_usd")
    print(f"Estimate for one question in {mode} mode on {model}: {cost_mod.usd_fine(est)} (cap {cost_mod.usd(cap_limit)})")
    if estimate_only:
        return 0
    if mode != "sources" and not (ws.wiki / "_index.md").exists():
        raise WikiError("The wiki has no _index.md yet: run wiki build first (or use --mode sources).")
    runner = Runner(ws, cfg, ledger)
    runner.check_ready()
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, est, cap_limit, approve_over_cap, "this question",
                             float(cfg.runner("reserve", 0.10)))
    run_dir = private_dir(ws.runs / "ask")
    task_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + f"-{mode}"
    rec = ask_once(ws, cfg, runner, question, mode, run_dir, task_id,
                   cap * (1 - float(cfg.runner("reserve", 0.10))), model)
    print()
    print(rec["answer"] or f"(no answer: the run ended with {rec['outcome']})")
    print()
    if rec["unknown_units"]:
        print("WARNING: cited units not in the manifest: " + ", ".join(rec["unknown_units"]))
    elif rec["cited_units"]:
        print(f"Cited units, all in the manifest: {', '.join(rec['cited_units'])}")
    if rec["malformed_tags"]:
        print(f"WARNING: {len(rec['malformed_tags'])} malformed source tag(s)")
    print(f"Cost {cost_mod.usd_fine(rec['cost_usd'])}; transcript saved for dreaming: {run_dir / (task_id + '.json')}")
    return 0 if rec["outcome"] == "success" else 2
