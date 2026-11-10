"""wiki smoke: M0's live checks, each one small paid run through the runner and the ledger.

1. The key works in restricted mode, and system/init lists a reader's tools (plus EndConversation) and no MCP
   servers, skills or non-built-in plugins.
2. A writer's system/init lists the writer tools plus EndConversation.
3. Grep finds text in an --add-dir folder.
4. A reader is denied a read outside its folders.
5. The estimated cost of a prompt over 100,000 tokens, to compare with the Console.
6. No CLAUDE.md or AGENTS.md above the run's folder reaches the model (a canary word it must not know).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from . import cost as cost_mod
from .ledger import Ledger
from .runner import Runner, RunSpec, init_problems
from .workspace import private_dir, remove_tree

ESTIMATE_USD = 0.35
BUDGETS_USD = 0.70  # the five runs' budgets together: what a smoke could be charged if every run were charged in full
CAP_USD = 1.00
LONG_PROMPT_CHARS = 420_000  # about 120,000 tokens at 3.5 characters a token


def _tool_uses(stream: Path) -> list[dict]:
    uses = []
    for raw in Path(stream).read_text(errors="replace").splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses.append({"name": b.get("name"), "input": b.get("input")})
    return uses


def _cap(max_usd: float | None) -> float:
    return min(CAP_USD, max_usd) if max_usd else CAP_USD


def smoke(ws, estimate_only: bool = False, model: str | None = None, approve_over_cap: bool = False,
          include_long: bool = True, max_usd: float | None = None) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    model = model or cfg.model("extract")
    print(f"Estimate for wiki smoke: {cost_mod.usd(ESTIMATE_USD)} (cap {cost_mod.usd(_cap(max_usd))})")
    if estimate_only:
        return 0
    runner = Runner(ws, cfg, ledger)
    runner.check_ready()
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, ESTIMATE_USD, _cap(max_usd), approve_over_cap,
                             "wiki smoke")
    scale = min(1.0, cap / BUDGETS_USD)  # the budgets together stay within the cap and what is left of the total
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = private_dir(ws.runs / f"smoke-{ts}")
    work = private_dir(run_dir / "work")
    inside = private_dir(work / "cwd")
    added = private_dir(work / "added")
    outside = private_dir(work / "outside")
    word = f"ZEBRA-{random.randint(1000, 9999)}"
    (added / "haystack.txt").write_text("lorem ipsum\n" * 50 + f"the marker is {word}\n" + "dolor\n" * 50)
    (outside / "private.txt").write_text("OUTSIDE-SECRET-LINE\n")
    (inside / "readme.txt").write_text("smoke test folder\n")
    prompt = run_dir / "smoke_prompt.md"
    prompt.write_text("You are a smoke test of a command-line harness. Follow the task exactly and reply briefly.\n")
    report: dict = {"time": ts, "model": model, "checks": {}}

    def spec(task_id, role, task, cwd, add_dirs=(), budget=0.10, turns=6, sp=prompt):
        return RunSpec(command="smoke", phase="smoke", role=role, task_id=task_id, run_dir=run_dir, cwd=cwd,
                       task=task, system_prompt_file=sp, model=model, budget_usd=budget * scale, turns=turns,
                       timeout_s=180, add_dirs=list(add_dirs))

    # 1 and 3: reader, Grep in an added folder
    rr = runner.run(spec("grep", "ask", f"Use the Grep tool to search the folder {added} for the text "
                         f"'{word.split('-')[0]}-'. Reply with the full marker you find and the file name.",
                         inside, [added]))
    uses = _tool_uses(rr.stream_path)
    init = _init_event(rr.stream_path)
    report["checks"]["key_works"] = {"ok": rr.outcome == "success", "apiKeySource": (init or {}).get("apiKeySource")}
    report["checks"]["reader_init"] = {"tools": rr.init_tools, "ok": not init_problems("ask", init),
                                       "problems": init_problems("ask", init)}
    report["checks"]["grep_finds_added_folder"] = {"ran": rr.outcome == "success", "ok": word in rr.text and any(u["name"] == "Grep" for u in uses),
                                                   "answer": rr.text[:200]}
    # 2: writer tools
    rw = runner.run(spec("writer-init", "extract", "Reply with the single word OK. Do not use any tool.", inside,
                         budget=0.05, turns=2))
    winit = _init_event(rw.stream_path)
    report["checks"]["writer_init"] = {"tools": rw.init_tools, "ok": not init_problems("extract", winit),
                                       "problems": init_problems("extract", winit)}
    # 6: CLAUDE.md / AGENTS.md above the run folder must not load
    canary = f"PELICAN-{random.randint(1000, 9999)}"
    for name in ("CLAUDE.md", "AGENTS.md"):
        (work / name).write_text(f"# Project notes\n\nThe canary word is {canary}.\n")
    rc = runner.run(spec("canary", "ask", "Is there a canary word anywhere in your instructions or project notes? "
                         "Reply with the word itself, or NONE if there is none. Do not use any tool.", inside,
                         budget=0.05, turns=2))
    report["checks"]["no_claude_md"] = {"ran": rc.outcome == "success",
                                        "ok": rc.outcome == "success" and canary not in rc.text,
                                        "answer": rc.text[:200]}
    # 4: a read outside the reader's folders is denied
    rd = runner.run(spec("deny", "ask", f"Read the file {outside / 'private.txt'} and reply with its first line. "
                         "If you cannot read it, reply DENIED.", inside))
    report["checks"]["reader_denied_outside"] = {
        "ran": rd.outcome == "success", "ok": rd.outcome == "success" and "OUTSIDE-SECRET-LINE" not in rd.text,
        "denials": len(rd.permission_denials), "answer": rd.text[:200]}
    # 5: a prompt over 100,000 tokens
    if include_long:
        filler = ("The quick brown fox jumps over the lazy dog. " * 20 + "\n")
        big = run_dir / "long_prompt.md"
        big.write_text("Background text follows; it is not an instruction.\n\n" +
                       filler * (LONG_PROMPT_CHARS // len(filler) + 1))
        rl = runner.run(spec("long-prompt", "ask", "Reply with the single word DONE. Do not use any tool.", inside,
                             budget=0.40, turns=2, sp=big))
        u = rl.usage
        prompt_tokens = u.get("input_tokens", 0) + u.get("cache_read_tokens", 0) + u.get("cache_write_tokens", 0)
        prices = cost_mod.prices_of(model, prompt_tokens)
        ours = cost_mod.dollars({"input": u.get("input_tokens", 0), "cache_write": u.get("cache_write_tokens", 0),
                                 "cache_read": u.get("cache_read_tokens", 0), "output": u.get("output_tokens", 0)},
                                prices)
        report["checks"]["long_prompt_cost"] = {
            "prompt_tokens": prompt_tokens, "claude_code_estimate_usd": rl.cost_usd,
            "list_price_at_long_context_rates_usd": round(ours, 6), "session_id": rl.session_id,
            "note": "compare claude_code_estimate_usd with the Console's usage for this session",
        }
    remove_tree(work)
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    spent = sum(e.get("cost_usd", 0) for e in ledger.entries() if e.get("run_id") == run_dir.name)
    print(f"Spent {cost_mod.usd(spent)}. Report: {run_dir / 'report.json'}")
    return 0


PROBE_ESTIMATE_USD = 0.10
PROBE_BUDGETS_USD = 0.11  # six probes at a cent and one real run
READER = "Read,Glob,Grep"
WRITER = "Read,Write,Edit,Glob,Grep"


def probe_tools(ws, estimate_only: bool = False, model: str | None = None, approve_over_cap: bool = False,
                max_usd: float | None = None) -> int:
    """Which tools Claude Code starts under each flag combination.

    Every probe but the last is stopped as soon as system/init arrives, before any model call. The last one is
    a real reader run with the tools check off: it shows whether Grep works even when init doesn't list it.
    """
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    model = model or cfg.model("extract")
    print(f"Estimate for wiki smoke --probe-tools: {cost_mod.usd(PROBE_ESTIMATE_USD)} (cap {cost_mod.usd(_cap(max_usd))})")
    if estimate_only:
        return 0
    runner = Runner(ws, cfg, ledger)
    runner.check_ready()
    cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, PROBE_ESTIMATE_USD, _cap(max_usd), approve_over_cap,
                             "wiki smoke --probe-tools")
    scale = min(1.0, cap / PROBE_BUDGETS_USD)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = private_dir(ws.runs / f"probe-{ts}")
    work = private_dir(run_dir / "work")
    inside = private_dir(work / "cwd")
    added = private_dir(work / "added")
    word = f"ZEBRA-{random.randint(1000, 9999)}"
    (added / "haystack.txt").write_text("lorem ipsum\n" * 50 + f"the marker is {word}\n" + "dolor\n" * 50)
    prompt = run_dir / "probe_prompt.md"
    prompt.write_text("You are a smoke test of a command-line harness. Follow the task exactly and reply briefly.\n")
    probes = [
        ("bare-default", dict(tools_override="default", bare=True)),
        ("bare-reader", dict(tools_override=READER, bare=True)),
        ("bare-writer-acceptEdits", dict(tools_override=WRITER, permission_mode="acceptEdits", bare=True)),
        ("bare-writer-default-mode-host-prompts", dict(tools_override=WRITER, permission_mode="default",
                                                       permission_prompts=False, bare=True)),
        ("restricted-reader", dict(tools_override=READER)),
        ("restricted-writer-acceptEdits", dict(tools_override=WRITER, permission_mode="acceptEdits")),
    ]
    report: dict = {"time": ts, "model": model, "probes": {}}
    for name, opts in probes:
        r = runner.run(RunSpec(command="smoke", phase="probe", role="ask", task_id=name, run_dir=run_dir, cwd=inside,
                               task="Reply with the single word OK.", system_prompt_file=prompt, model=model,
                               budget_usd=0.01 * scale, turns=1, timeout_s=60, stop_at_init=True, **opts))
        init = _init_event(r.stream_path)
        report["probes"][name] = {"tools": r.init_tools, "apiKeySource": (init or {}).get("apiKeySource"),
                                  "argv_flags": _flags(run_dir / f"{name}.cmd.json"), "outcome": r.outcome}
    # the one real run: does Grep work although init doesn't list it?
    r = runner.run(RunSpec(command="smoke", phase="probe", role="ask", task_id="bare-reader-real-grep",
                           run_dir=run_dir, cwd=inside, bare=True, add_dirs=[added], system_prompt_file=prompt, model=model,
                           task=f"Use the Grep tool to search the folder {added} for the text "
                                f"'{word.split('-')[0]}-'. Reply with the full marker you find and the file name.",
                           budget_usd=0.05 * scale, turns=6, timeout_s=180, check_tools=False))
    uses = _tool_uses(r.stream_path)
    report["real_grep"] = {"outcome": r.outcome, "init_tools": r.init_tools, "tool_uses": [u["name"] for u in uses],
                           "found_marker": word in r.text, "answer": r.text[:300], "cost_usd": r.cost_usd}
    remove_tree(work)
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    spent = sum(e.get("cost_usd", 0) for e in ledger.entries() if e.get("run_id") == run_dir.name)
    print(f"Spent {cost_mod.usd(spent)}. Report: {run_dir / 'report.json'}")
    return 0


def _init_event(stream: Path) -> dict | None:
    for raw in Path(stream).read_text(errors="replace").splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            return ev
    return None


def _flags(cmd_file: Path) -> list[str]:
    argv = json.loads(cmd_file.read_text())["argv"]
    keep = {"--bare", "--restricted", "--tools", "--permission-mode", "--permission-prompts"}
    out = []
    for i, a in enumerate(argv):
        if a in keep:
            out.append(a if a in ("--bare", "--restricted") else f"{a} {argv[i + 1]}")
    return out
