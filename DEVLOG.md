# DEVLOG

One entry per milestone: what changed, what it cost. Costs are paid runs made by the tool (the ledger);
Claude Code's own development session is billed separately and doesn't count.

## M0 Scaffold — done; the long-prompt cost still needs comparing with the Console (2026-11-08)

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

### First live smoke (approved; 2026-11-08, Claude Code 2.1.319, Haiku)

- The key works in bare mode: `system/init` reports `apiKeySource: ANTHROPIC_API_KEY`, model `claude-haiku-5-5`.
- Tools don't match: `--tools Read,Write,Edit,Glob,Grep` started only `["Edit","Read"]`, and
  `--tools Read,Glob,Grep` only `["Read"]`. `Write`, `Glob` and `Grep` were dropped silently, with nothing on stderr. The
  runner killed all four runs at init, as designed (`bad_tools`), so the Grep, denial and long-prompt checks
  never ran. No role gained a tool, so nothing unsafe happened, but without Glob/Grep the readers can't search, and the
  writers can't create files without Write.
- Reported cost $0.00 for every run that returned a result; the Grep run returned no result event, so the ledger charged its
  full share ($0.10). Real API usage was probably about zero.
- Open: why 2.1.319 drops these tools. The changelog for 2.1.309–2.1.319 says nothing about it. M0 waits on the owner.

### Tool probe (`wiki smoke --probe-tools`, approved; 2026-11-08)

Added `wiki smoke --probe-tools`. It starts runs through the runner and stops each one at `system/init`, before
any model call. A final real reader run has the tools check turned off. `system/init` tools:

| Flags | Tools asked for | Tools started |
| --- | --- | --- |
| `--bare --tools default` | default | Bash, Edit, Read |
| `--bare` | Read, Glob, Grep | Read |
| `--bare`, acceptEdits | Read, Write, Edit, Glob, Grep | Edit, Read |
| `--bare`, default mode, host prompts | Read, Write, Edit, Glob, Grep | Edit, Read |
| `--restricted --strict-mcp-config --disable-slash-commands` (no `--bare`) | Read, Glob, Grep | Glob, Grep, Read |
| same, acceptEdits | Read, Write, Edit, Glob, Grep | Edit, Glob, Grep, Read, Write |

- Bare mode in 2.1.319 has its own fixed tool set (Bash, Edit, Read). `--tools` can only narrow it, never add
  Write, Glob or Grep. Permission mode and permission prompts make no difference.
- The real reader run tried Grep anyway. The call failed and Grep was not usable, so init's list is accurate.
- Restricted mode without `--bare` starts exactly the role tools (EndConversation isn't listed). The key still
  comes from `ANTHROPIC_API_KEY`. It does load four built-in plugins (agents-md, telemetry, privacy,
  plugin-authoring), and it does not skip what `--bare` skips (CLAUDE.md discovery, auto-memory, hooks, keychain reads).
  Switching away from `--bare` changes SPEC's design, so it waits on the owner.

Cost: $0.0006 (ledger). M0 total: $0.10 of the $1 allowance.

Cost so far: $0.10 (ledger) of the $1 allowance.

### Switch to restricted mode (owner-approved change from SPEC, 2026-11-08)

The owner approved replacing `--bare` with `--restricted --strict-mcp-config --disable-slash-commands`. SPEC's
`--bare` can't give the roles Write, Glob or Grep on 2.1.319. To make up for what bare mode skipped:
- Every run gets `CLAUDE_CONFIG_DIR=<workspace>/runs/.claude-config` (0700, empty), set by the runner, not passed
  through. No user settings, plugins, hooks or OAuth login can load, so a missing key can't fall back to a login.
- The runner sets `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. These names come from the 2.1.319 binary, not the docs, so the smoke has a canary check:
  a CLAUDE.md and AGENTS.md above the run folder name a word the model must not know.
- The `system/init` check now also fails a run that reports an MCP server, a skill or a plugin that isn't
  built in (`init_problems`, recorded on the ledger line).
- `min_claude_version` is now 2.1.319: `--restricted` is new, and this is the only version tested.
- Tests: argv defaults, the private config dir and environment, extra MCP servers and plugins failing a run
  (fake claude `extras` mode), and the canary check offline.

### Second live smoke, restricted mode (2026-11-08, Claude Code 2.1.319, Haiku)

Every check passed (`runs/smoke-20261108T082414Z/report.json`):
- Key: `apiKeySource: ANTHROPIC_API_KEY`.
- Reader init: Glob, Grep, Read. Writer init: Edit, Glob, Grep, Read, Write. There were no MCP servers or skills, and
  the only plugins were built-in ones (agents-md, privacy, plugin-authoring). EndConversation isn't listed in this version.
- Grep found the marker in an `--add-dir` folder (file and line).
- A reader asked to read a file outside its folders answered DENIED, with 1 permission denial.
- Canary: with a CLAUDE.md and an AGENTS.md above the run folder, the model answered NONE, so
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS` works, and the agents-md plugin loads nothing either.
- Long prompt: 174,400 prompt tokens. Claude Code's estimate was $0.1073, the same as our list price at Haiku's long-prompt
  rates ($0.1073), so Claude Code already applies the long-prompt price. The owner still needs to compare it with the
  Console (session `c3556352-6a6a-42c4-bede-ee6ca79afd88`).
- The private config folder holds only Claude Code's own state (no key: checked for `sk-ant` and
  `customApiKeyResponses`).

Found and fixed: concurrent eval runs raced rewriting the read-only grader prompt (flaky `PermissionError`).
`write_prompt` now writes once, atomically, under a lock.

M0 cost: $0.21 (ledger) of the $1 allowance. Three smoke commands (16 runs): $0.10 of it is the charge for one run that
never returned a result, and $0.11 is real spend.

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

## M2–M4 — offline parts written ahead; every paid step awaits approval (2026-11-08)

M2, M3 and M4 each end in paid runs, and M0's live checks come first, so these milestones are not done. Their
code is written and tested offline against the fake claude, so the paid steps can start as soon as the owner
approves them:
- M2: the role prompts and the build are ready (see M1); the quickstart-tier build itself is paid
  (allowance $8). After it: compare estimate and actual in the ledger (the estimator recalibrates itself from
  ledger averages per phase and model) and tune turn limits.
- M3: `wiki ask` in `wiki` / `sources` / `both` modes (one fresh read-only session, cited units checked against
  the manifest, transcripts to `runs/ask/`), `wiki eval` (every question in every mode, Sonnet grader in a
  temporary folder holding one answer and its criteria, report with score and cost per mode, traps refused,
  unknown citations and break-even; transcripts to `runs/eval/`, never used for dreaming).
  `examples/sec-deal/core-tier.toml` holds the notebook's 6 held-out and 4 demo questions with criteria from
  its comments and notes; the demo buyer-side question's criteria stay generic (parent, purchaser, merger
  subsidiary, Permira funds) because the notebook names no entities for it. Still to do: draft
  `quickstart-tier.toml` from the fetched quickstart-tier documents (needs `EDGAR_USER_AGENT`), owner approval
  of that file, then the paid eval (allowance $4).
- M4: `wiki dream` (transcripts since the newest `dream-accepted-*` tag, at least 5; branch `dream/<ts>` in its
  own worktree; parallel read-only analysts; one apply run that may only add to addenda/open_questions/
  `_aliases.md` and only mark `retracted`/`valid_to` elsewhere; recompile and reindex on the branch;
  `wiki check --against` the base; summary of findings, files changed, conflicts and retractions;
  `--accept` merges and tags, `--discard` deletes; never merges on its own). The paid dream over ≥5
  quickstart-tier transcripts awaits approval (allowance $4).
- README.md covers setup, every command, costs, the Console spend limit and the cron line for dreaming.

Tests: ask in each mode (working folders and added folders per SPEC's table, eval files never reachable),
the trap refused, eval end to end and its scoring, question-file validation, dream accept / discard /
fewer than 5 transcripts / a dream that changes a fact line is refused.

Cost: $0.00. Total spent so far: $0.21 of $50 (all M0).
