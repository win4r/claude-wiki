"""wiki ask: one fresh read-only session per question, in wiki, sources or both mode."""

from __future__ import annotations

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


def ask_once(ws, cfg, runner: Runner, question: str, mode: str, run_dir: Path, task_id: str, budget: float,
             model: str, command: str = "ask") -> dict:
    spec = spec_for(ws, cfg, mode, question, run_dir, task_id, budget, model, command)
    rr = runner.run(spec)
    manifest = Manifest.load(ws.manifest_path)
    units, malformed = cited_units(rr.text)
    unknown = [u for u in units if u not in manifest.units]
    record = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command, "question": question, "mode": mode, "model": model, "answer": rr.text,
        "outcome": rr.outcome, "cost_usd": rr.cost_usd, "session_id": rr.session_id, "turns": rr.turns,
        "cited_units": units, "unknown_units": unknown, "malformed_tags": malformed,
        "refused": refused(rr.text), "stream": str(rr.stream_path),
    }
    (Path(run_dir) / f"{task_id}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return record


def ask(ws, question: str, mode: str = "both", estimate_only: bool = False, max_usd: float | None = None,
        model: str | None = None, approve_over_cap: bool = False) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    model = model or cfg.model("ask")
    est = cost_mod.estimate_question(ledger.averages(), mode, model)
    cap_limit = min(cfg.cap("question_usd"), max_usd) if max_usd else cfg.cap("question_usd")
    print(f"Estimate for one question in {mode} mode on {model}: {cost_mod.usd(est)} (cap {cost_mod.usd(cap_limit)})")
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
    print(f"Cost {cost_mod.usd(rec['cost_usd'])}; transcript saved for dreaming: {run_dir / (task_id + '.json')}")
    return 0 if rec["outcome"] == "success" else 2
