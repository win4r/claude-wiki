# DEVLOG

One entry per milestone: what changed, what it cost. Costs are paid runs made by the tool (the ledger);
Claude Code's own development session is billed separately and doesn't count.

## M0 Scaffold — offline part done, live checks awaiting approval (2026-11-08)

What changed:
- Repo layout, `pyproject.toml` (entry point `wiki`, extra `convert`), `NOTICE` (MIT, claude-quickstarts).
- `config.py` (`wiki.toml` template: models per role, caps, turn limits and timeouts from SPEC), `ledger.py`
  (append-only `ledger.jsonl`, mode 0600, summaries and per-phase/model averages), `cost.py` (quickstart price
  table incl. Haiku 5.5's long-prompt rates, build/question/grade/dream estimates, `authorize` = hard stop at the
  total + ask over a cap, `share` = proportional split with a 10% reserve), `runner.py` (the only code that
  starts `claude`: bare mode, role tools, `acceptEdits` for writers / `dontAsk` for readers,
  `--permission-prompts none`, `--max-budget-usd`, `--max-turns`, stream-json saved line by line, `system/init`
  tools checked against role tools + `EndConversation` (a mismatch kills the run), result-event parsing,
  ledger line on every run, full share charged when no result arrives, SIGINT then SIGTERM on timeout,
  a semaphore for `--concurrency`, version check ≥ 2.1.302, env pass-through limited to what claude needs).
- `cli.py` skeleton with every command; `wiki smoke` runs M0's live checks through the runner.
- `tests/fake_claude.py` (chosen with `WIKI_CLAUDE_BIN`): canned work per role plus misbehaviours
  (outside, markers, input, fail, noresult, overcap, maxturns, badtools, hang, lifecycle).
- Stream-json field names pinned from the Claude Code docs (headless, CLI reference, Agent SDK types):
  `system/init.tools`, `result.subtype` (`success`, `error_max_turns`, `error_max_budget_usd`,
  `error_during_execution`), `total_cost_usd`, `usage`/`modelUsage`, `num_turns`, `permission_denials`.
  `--max-turns` is documented but missing from `claude --help` on 2.1.319; `wiki smoke` will confirm it.

Not done yet (needs the owner's approval, allowance $1): the live smoke runs. `wiki smoke` records whether
the key works in bare mode, `system/init` lists the role tools plus EndConversation, Grep finds text in an
added folder, readers are denied a read outside their folders, and the estimated cost of a prompt over
100,000 tokens (to compare with the Console).

Cost: $0.00 (no paid runs).

## M1 Offline pipeline — done (2026-11-08)

What changed:
- `wiki init`, `wiki add` (`convert.py`: txt/md/html/csv/eml with the standard library; docx/xlsx/pptx/pdf with
  the `convert` extra; scanned PDFs refused), `wiki fetch-example` (the quickstart's fetch script, changed to
  refuse without `EDGAR_USER_AGENT` and to write into the workspace; the two synthetic files with their source
  lines; question sets copied into `repo/eval/`).
- `manifest.py`: slugs, units (`-part<n>`, each part keeps the `[SOURCE]` line plus "part n of N", split at line
  boundaries), sha256 diffs (new / changed / unchanged / removed), batching. Design choice: `wiki add` splits a
  long document into units when it converts it, so `docs/` always holds one read-only `.txt` per unit and
  incremental builds compare units by sha256; phase 2 packs those units into batch lists.
- `grammar.py` (tags in SPEC's field order, fact lines, notes sections, the aliases table, markers),
  `check.py` (every rule in SPEC's table, plus `--against`), `compile.py` (entity pages from fact lines grouped
  by unit, current picture kept across recompiles, fact pages past 80 KB, pages dropped for slugs left with no
  facts).
- `phases.py`: the eight phases, one commit per phase, a git worktree per writing run, ownership checks
  (owned paths; markers-only for Compile; lifecycle-only marks for dream apply), read-only input copies
  re-hashed after every run, documents and `_schema.md` re-verified after every phase, retry once (never after
  a budget or turn-limit stop), extraction failure stops the build after phase 3, `state.json` per task and
  `--resume`, the "check what ran" checks, incremental builds, Resolve's 10-question limit, and `wiki check`
  at the end. Per-run budgets are each run's share of the build cap; a run that would pass the cap or the
  total is not started.
- Role prompts (`prompts/`) adapted from the quickstart for single writers, and `schemas/sec-deal/`
  (`_schema.md` from WIKI_SCHEMA, `steering.md` from STEERING with red flags routed to `## red-flags`). These are
  drafts until the M2 build tests them.
- Tests: conversion of each type, slugs/units/splitting/batching/manifest diffs, grammars, each `wiki check`
  rule, the fixture build (three documents, one split into three parts) passing `wiki check`, `--resume` after
  a failure, incremental builds, every guard (outside paths, outside markers, edited input, edited document,
  failure + retry, budget and turn stops not retried, no result charged its full share, bad tools, cap and
  total stops, the lock and stale locks).

Found and fixed while testing: concurrent runs raced writing the shared prompt file (now written once per
run); one unit's facts over 80 KB stayed on a single fact page (now split by line).

Cost: $0.00 (allowance $0).
