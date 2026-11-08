"""wiki smoke: M0's live checks, each one small paid run through the runner and the ledger.

1. The key works in bare mode, and system/init lists a reader's tools plus EndConversation.
2. A writer's system/init lists the writer tools plus EndConversation.
3. Grep finds text in an --add-dir folder.
4. A reader is denied a read outside its folders.
5. The estimated cost of a prompt over 100,000 tokens, to compare with the Console.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from . import cost as cost_mod
from .ledger import Ledger
from .runner import ROLE_TOOLS, Runner, RunSpec
from .workspace import private_dir, remove_tree

ESTIMATE_USD = 0.35
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


def smoke(ws, estimate_only: bool = False, model: str | None = None, approve_over_cap: bool = False,
          include_long: bool = True) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    model = model or cfg.model("extract")
    print(f"Estimate for wiki smoke: {cost_mod.usd(ESTIMATE_USD)} (cap $1.00)")
    if estimate_only:
        return 0
    runner = Runner(ws, cfg, ledger)
    runner.check_ready()
    cost_mod.authorize(ledger.spent(), cfg.total_usd, ESTIMATE_USD, 1.0, approve_over_cap, "wiki smoke")
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
                       task=task, system_prompt_file=sp, model=model, budget_usd=budget, turns=turns,
                       timeout_s=180, add_dirs=list(add_dirs))

    # 1 and 3: reader, Grep in an added folder
    rr = runner.run(spec("grep", "ask", f"Use the Grep tool to search the folder {added} for the text "
                         f"'{word.split('-')[0]}-'. Reply with the full marker you find and the file name.",
                         inside, [added]))
    uses = _tool_uses(rr.stream_path)
    report["checks"]["key_works_in_bare_mode"] = rr.outcome == "success"
    report["checks"]["reader_init_tools"] = {"tools": rr.init_tools, "ok": rr.init_tools is not None and
                                             set(rr.init_tools) - {"EndConversation"} == set(ROLE_TOOLS["ask"])}
    report["checks"]["grep_finds_added_folder"] = {"ok": word in rr.text and any(u["name"] == "Grep" for u in uses),
                                                   "answer": rr.text[:200]}
    # 2: writer tools
    rw = runner.run(spec("writer-init", "extract", "Reply with the single word OK. Do not use any tool.", inside,
                         budget=0.05, turns=2))
    report["checks"]["writer_init_tools"] = {"tools": rw.init_tools, "ok": rw.init_tools is not None and
                                             set(rw.init_tools) - {"EndConversation"} == set(ROLE_TOOLS["extract"])}
    # 4: a read outside the reader's folders is denied
    rd = runner.run(spec("deny", "ask", f"Read the file {outside / 'private.txt'} and reply with its first line. "
                         "If you cannot read it, reply DENIED.", inside))
    report["checks"]["reader_denied_outside"] = {
        "ok": "OUTSIDE-SECRET-LINE" not in rd.text, "denials": len(rd.permission_denials), "answer": rd.text[:200]}
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
