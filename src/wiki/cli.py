"""The wiki command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import WikiError, __version__, gitops
from . import cost as cost_mod
from .workspace import Workspace, find_workspace, init_workspace


def _paid(p: argparse.ArgumentParser) -> None:
    p.add_argument("--estimate", action="store_true", help="print the cost estimate and exit")
    p.add_argument("--max-usd", type=float, help="lower this command's cap")
    p.add_argument("--model", help="a model alias for every run of this command")
    p.add_argument("--concurrency", type=int, default=None, help="runs at once (default 4)")
    p.add_argument("--approve-over-cap", action="store_true",
                   help="the owner approves, once, an estimate above the command's cap")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wiki", description="Build, query and maintain a Markdown knowledge wiki.")
    ap.add_argument("--version", action="version", version=f"wiki {__version__}")
    ap.add_argument("--workspace", "-w", help="the workspace folder (default: WIKI_WORKSPACE or the nearest upward)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a workspace")
    p.add_argument("dir")
    p.add_argument("--schema-dir", help="folder with _schema.md and steering.md (default: schemas/sec-deal)")

    p = sub.add_parser("fetch-example", help="download the example SEC filings and add them")
    p.add_argument("--tier", required=True, choices=["quickstart", "core"])

    p = sub.add_parser("add", help="convert documents and record them in the manifest")
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("build", help="run the pipeline over new or changed documents (paid)")
    p.add_argument("--resume", action="store_true", help="finish an interrupted build")
    _paid(p)

    p = sub.add_parser("check", help="lint the wiki and enforce its invariants")
    p.add_argument("--against", help="also compare per-unit fact lines with this commit")

    p = sub.add_parser("ask", help="answer a question with a source tag on every fact (paid)")
    p.add_argument("question")
    p.add_argument("--mode", choices=["wiki", "sources", "both"], default="both")
    _paid(p)

    p = sub.add_parser("dream", help="consolidate the wiki from ask transcripts on a review branch (paid)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--accept", action="store_true", help="merge the open dream branch and tag it")
    g.add_argument("--discard", action="store_true", help="delete the open dream branch")
    _paid(p)

    p = sub.add_parser("eval", help="ask every question in every mode and grade the answers (paid)")
    p.add_argument("file")
    p.add_argument("--modes", default="wiki,sources,both", help="comma-separated modes to run")
    _paid(p)

    sub.add_parser("cost", help="show the ledger")
    sub.add_parser("status", help="units, pages, last build and dream, open review branch")

    p = sub.add_parser("smoke", help="M0's live checks of Claude Code (paid, about $0.35)")
    p.add_argument("--no-long-prompt", action="store_true")
    p.add_argument("--probe-tools", action="store_true",
                   help="diagnose which tools Claude Code starts under each flag (about $0.10)")
    _paid(p)
    return ap


def workspace_of(args) -> Workspace:
    if args.workspace:
        return Workspace(Path(args.workspace)).check_exists()
    return find_workspace()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return dispatch(args)
    except WikiError as why:
        print(f"wiki: {why}", file=sys.stderr)
        return why.code
    except KeyboardInterrupt:
        print("wiki: interrupted", file=sys.stderr)
        return 130


def dispatch(args) -> int:
    if args.cmd == "init":
        ws = init_workspace(Path(args.dir), Path(args.schema_dir) if args.schema_dir else None)
        print(f"Created workspace {ws.root}")
        return 0
    ws = workspace_of(args)
    if args.cmd == "check":
        return cmd_check(ws, args.against)
    if args.cmd == "cost":
        return cmd_cost(ws)
    if args.cmd == "status":
        return cmd_status(ws)
    paid = args.cmd in ("build", "ask", "dream", "eval", "smoke")
    if paid and getattr(args, "estimate", False) and not (args.cmd == "dream" and (args.accept or args.discard)):
        return _run(ws, args)  # an estimate spends nothing and writes nothing: no lock needed
    with ws.lock(args.cmd):
        return _run(ws, args)


def _run(ws, args) -> int:
    if args.cmd == "fetch-example":
        from .documents import fetch_example

        return fetch_example(ws, args.tier)
    if args.cmd == "add":
        from .documents import add

        return add(ws, args.paths)
    if args.cmd == "build":
        from .phases import build

        return build(ws, resume=args.resume, estimate_only=args.estimate, max_usd=args.max_usd, model=args.model,
                     concurrency=args.concurrency, approve_over_cap=args.approve_over_cap)
    if args.cmd == "ask":
        from .ask import ask

        return ask(ws, args.question, mode=args.mode, estimate_only=args.estimate, max_usd=args.max_usd,
                   model=args.model, approve_over_cap=args.approve_over_cap)
    if args.cmd == "dream":
        from . import dream

        if args.accept:
            return dream.accept(ws)
        if args.discard:
            return dream.discard(ws)
        return dream.dream(ws, estimate_only=args.estimate, max_usd=args.max_usd, model=args.model,
                           concurrency=args.concurrency, approve_over_cap=args.approve_over_cap)
    if args.cmd == "eval":
        from .evaluate import run_eval

        modes = tuple(m.strip() for m in args.modes.split(",") if m.strip())
        return run_eval(ws, Path(args.file), estimate_only=args.estimate, max_usd=args.max_usd, model=args.model,
                        concurrency=args.concurrency, approve_over_cap=args.approve_over_cap, modes=modes)
    if args.cmd == "smoke":
        from .smoke import probe_tools, smoke

        if args.probe_tools:
            return probe_tools(ws, estimate_only=args.estimate, model=args.model,
                               approve_over_cap=args.approve_over_cap)
        return smoke(ws, estimate_only=args.estimate, model=args.model, approve_over_cap=args.approve_over_cap,
                     include_long=not args.no_long_prompt)
    raise WikiError(f"unknown command {args.cmd}")


def cmd_check(ws, against: str | None) -> int:
    from .check import errors, report, run_checks
    from .manifest import Manifest

    findings = run_checks(ws.wiki, Manifest.load(ws.manifest_path), repo=ws.repo, against=against)
    print(report(findings))
    return 1 if errors(findings) else 0


def cmd_cost(ws) -> int:
    from .ledger import Ledger

    cfg = ws.config()
    led = Ledger(ws.ledger_path)
    spent = led.spent()
    s = led.summary()
    print(f"Spent {cost_mod.usd(spent)} of {cost_mod.usd(cfg.total_usd)}; remaining {cost_mod.usd(cfg.total_usd - spent)}")
    print(f"{sum(s['runs'].values())} paid run(s)")
    for cmd, v in sorted(s["by_command"].items()):
        print(f"  {cmd:8} {cost_mod.usd(v):>9}  ({s['runs'][cmd]} runs)")
        for (c, phase), pv in sorted(s["by_phase"].items()):
            if c == cmd:
                print(f"    {phase:16} {cost_mod.usd(pv):>9}")
    return 0


def cmd_status(ws) -> int:
    from .check import kind_of, read_wiki
    from .manifest import Manifest

    m = Manifest.load(ws.manifest_path)
    by_status: dict[str, int] = {}
    for rec in m.units.values():
        by_status[rec["status"]] = by_status.get(rec["status"], 0) + 1
    files = read_wiki(ws.wiki)
    kinds: dict[str, int] = {}
    for p in files:
        kinds[kind_of(p)] = kinds.get(kind_of(p), 0) + 1
    print(f"Workspace {ws.root}")
    print(f"Units: {len(m.units)} (" + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())) + ")")
    print(f"Pages: {len(files)} (" + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")")
    builds = sorted(ws.runs.glob("build-*/state.json"))
    if builds:
        st = json.loads(builds[-1].read_text())
        state = "finished" if st.get("finished") else ("stopped: " + st.get("stopped", "") if st.get("stopped") else "running or interrupted")
        print(f"Last build: {st['build_id']} ({state}; spent {cost_mod.usd(st.get('spent', 0) or 0)})")
    else:
        print("Last build: none")
    dreams_file = ws.runs / "dreams.json"
    dreams = json.loads(dreams_file.read_text()) if dreams_file.exists() else []
    print(f"Last dream: {dreams[-1]['ts']} ({dreams[-1]['status']})" if dreams else "Last dream: none")
    d = next((x for x in reversed(dreams) if x["status"] in ("review", "failed-check")), None)
    branches = gitops.branches(ws.repo, "dream/*")
    print(f"Open review branch: {d['branch']} ({d['status']})" if d else
          ("Open review branch: " + ", ".join(branches) if branches else "Open review branch: none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
