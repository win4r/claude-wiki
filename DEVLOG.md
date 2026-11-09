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

## M2 Quickstart build — done (2026-11-08)

`wiki fetch-example --tier quickstart` downloaded the 8 SEC filings and added the 2 synthetic files: 10 units,
92 KB of text, one batch. The owner gave the EDGAR contact in chat; it was passed only in the environment of that one
command and appears in no workspace file. The paid build was approved at an estimate of $0.80 (cap $6).

Result: `build-20261108T095158Z` finished in 24 minutes, with every task succeeding on its first attempt. `wiki check` found 0
errors and 1 warning: the wiki is 284 KB against a 92 KB corpus, which is expected because entity pages repeat the per-unit
facts. It also left 1 note (`entities/squarespace.md`, 22.6 KB, the deal hub). The build produced 104 pages, including 46
entity pages, every unit's notes, addenda, metrics, relations and open questions, 3 contract files, `_index.md` with a
domain map, and `escalations.md` with 20 red flags in order, the TopCo cash sales first. A spot check of
`squarespace-tender-offer` showed source tags on every fact line, with the offer-expiry CONFLICT recorded between the Form 4 and the 8-K.

| Phase | Estimate | Actual | Turns (limit) |
| --- | --- | --- | --- |
| extract | $0.34 | $0.38 | 67 (150) |
| check | $0.43 | $0.49 | 86 (150) |
| resolve | $0.01 | $0.02 | 27 (150) |
| link | $0.01 | $0.03 | 6 (60) |
| compile (5 runs) | $0.01 | $0.05 | 19–24 (40) |
| index | $0.01 | $0.03 | 7 (60) |
| total | $0.80 | $1.00 | |

Tuning after the run:
- Turn limits are left as they are: the busiest run used 86 of 150.
- Per-run budgets were too tight for the small phases. Each run's share is proportional to its estimate, so a compile run
  got a $0.014 cap and one used $0.0135 of it. A budget stop isn't retried, so a stop there would have failed the build. Now
  each run gets at least `runner.min_run_usd` ($0.10), taken from the larger shares, and the shares still sum to at most
  90% of the cap.
- The estimate assumed 4 compile groups and the build needed 5. The unplanned 5th group used a flat $0.50
  fallback; it now gets its phase's largest share.
- The next estimate recalibrates from these ledger figures (per run and per character, by phase and model).

Cost: $1.00 (allowance $8). Total spent so far: $1.21 of $50.

## M3 Ask and eval — done (2026-11-08)

The owner approved `quickstart-tier.toml` (8 questions). It is committed in the tool repo and copied to
`repo/eval/`. The paid eval was approved at an estimate of $0.96 (cap $3) and spent **$0.24**: 24 answers and 24 Sonnet
grades. Report: `repo/eval/report-quickstart-tier-20261108T103252Z.md`.

| Mode | Score | Trap refused | Mean cost per answer |
| --- | --- | --- | --- |
| wiki | 90% (27/30) | yes | $0.0024 |
| sources | 97% (29/30) | yes | $0.0015 |
| both | 83% (25/30) | yes | $0.0027 |

What the answers show:
- **A placement gap, not a wrong fact** (corrected after checking the wiki during M4). The $400,000,000 Tock price
  *is* in the wiki: in `notes/8-k-20240621.md` under the `squarespace` heading, and on the `squarespace` and
  `american-express-travel-related-services` pages. The `tock` and `tock-securities-purchase-agreement` pages don't
  carry it, and the wiki-mode reader, looking only at those pages, wrongly said the filing doesn't state the
  price. In a later wiki-mode ask the reader found it.
- Both mode missed the repaid JPMorgan facility ("the wiki does not cover this"), which wiki mode found. Both mode also left
  out two of the vendor-extract near-miss rows. Answers in both mode do worse than wiki mode on this set, which isn't what you'd expect.
- **Citations of things that aren't units.** Wiki- and both-mode answers sometimes cite an entity slug (`squarespace`,
  `block`) or a path (`open_questions`) as `source:`. The eval flags each of these, as designed. The ask prompts should insist on
  unit ids; that's a prompt fix for the next eval.
- **Break-even: never** on this tier. With a 92 KB corpus, reading the documents directly costs less per answer ($0.0015)
  than going through the wiki ($0.0024). The core tier, with its much larger corpus, is the real test. The report now shows
  sub-cent costs to four decimals and explains a break-even of "never" (it showed $0.00 everywhere before).

Cost: $0.24 (allowance $4). Total spent so far: $1.45 of $50.

## M4 Dream — done (2026-11-08)

The owner approved M4. It ran 5 wiki-mode asks (about $0.01 in all; transcripts are in `runs/ask/`), then `wiki dream`, estimated at
$1.75 (cap $3) and actually $0.09. That covered one analyst run, one apply run, a recompile of 5 pages and a reindex.

Branch `dream/20261108T104218Z` held 6 findings: 1 WRONG, 3 MISS, 1 UNPLACED and 1 mislabelled CONFLICT.
- The fee-funding relation ("guarantees certain obligations of Spaceship Purchaser") was retracted. An addendum says the
  guarantee covers the "Buyer Parties", Merger Sub included, and lists the 12 FFA Investor funds.
- Anton Levy's share-count CONFLICT was really two different securities (25,390 Class A shares and 6,197 RSUs). An addendum explains
  this and adds the real red flag: the disposal is dated 2024-10-14 but reported "pursuant to the Offer", which expired on 2024-10-11.
- The June 21 Tock 8-K was added to Courtenay O'Connor's signings. The 12 fund names became `permira` aliases, and a new open question asks
  whether Permira Advisers LLC advises them.

`wiki check --against` the base passed. The summary (6 findings, 16 files, 0 conflicts marked, 1 line retracted)
matched the diff. The owner reviewed it and accepted it: merged `--no-ff` and tagged
`dream-accepted-20261108T104218Z`. `wiki check` on main: 0 errors.

Follow-ups (done after M4, before M5; prompts untested until the next paid run):
- The index run rewrote `README.md` and dropped what the per-unit folders hold, how `_aliases.md` maps names, and the
  "computed" label. The index prompt should keep those points.
- The ask prompts should require unit ids in `source:` tags (see M3).
- `wiki ask` prints "Cost $0.00" for sub-cent answers and should use the four-decimal format.

Cost: $0.10 (allowance $4). Total spent so far: $1.54 of $50.

## M5 Acceptance build — build and eight evals done; score and cost not yet met together (2026-11-08)

The core-tier build is paid for and checked; the core-tier eval has not run yet.
- Build: filed every unit, with no ownership violations (the 25 `violations.txt` files are all format
  rejections that a retry fixed). Every source file is unchanged. It cost **$11.44** against an estimate of
  $25.50 (cap approved over by the owner).
- Fixes made during the build, each tested offline: extract prompt spells out the checked formats; a retry
  is told why its first attempt was rejected, and a resumed task is told why it failed; unspent cap moves to
  unfinished tasks before each phase (half the pool, shares only rise); git worktree add/remove are
  serialized; `escalations.md` over the page limit is split by the program into `escalations-N.md`.
- Design change (owner-approved): entity pages keep the Current picture and a `## Sources` table pointing
  to the per-unit `notes/` and `relations/` files, instead of copying every fact line into `<slug>-facts-N`
  pages. This makes the wiki **2,585,746 bytes against a 2,785,858-byte corpus (93%)**, where it was larger
  than the corpus before. The recompile was done offline (no paid runs). Five `_index.md` lines that
  listed the removed `squarespace-facts-*` pages were deleted by hand (workspace commit bcb3400).
- `wiki check` on the core wiki: 0 errors, 173 warnings (missing link targets), 25 notes.
- The core workspace's ledger total is set to $48.46, the $50 less what the other workspaces spent.

- First core-tier eval (approved; estimate $1.20, cap $3, spent **$0.40**; report
  `repo/eval/report-core-tier-20261108T151422Z.md`): wiki 78% (32/41), sources 80%, both 76%. The trap was
  refused in every mode. Median cost: wiki $0.0030, sources $0.0032, both $0.0038.
  - 4 answers were ungraded because the grader corrected itself and printed a second JSON object. Fixed:
    the last valid object wins, and the grader prompt asks for one object. Re-parsing the saved grader
    output with no new spend gives wiki 88% (36/41), sources 80%, both 90%. The committed report stays as
    it was run.
  - Wiki and both answers cited 8 ids outside the manifest: `_schema.md`, entity slugs, exhibit names and
    "a via b" forms. The prompts already forbade slugs. Fixed in the program: an answer citing ids
    outside the manifest is asked again once, with each bad id explained (an entity page's units come
    from its Sources table). Both runs' cost is counted, and the report counts these retries.

- Second core-tier eval (approved; estimate $0.40, spent **$0.38**; report
  `repo/eval/report-core-tier-20261109T012606Z.md`): wiki 78% (32/41), sources 85%, both 90%. The trap was
  refused in every mode, every answer was graded, and **no cited id is outside the manifest** (one both-mode
  answer was asked again). Median cost: wiki $0.0034, sources $0.0025, both $0.0037.
  - The two evals disagree. Wiki mode went from 88% (re-parsed) to 78%, and its median cost from below
    sources mode to above it. With 10 questions, one answer swings the score by 2–3 points and the median
    by a fraction of a cent. Neither criterion is settled by a single run.
  - Wiki-mode misses are answers that stop early, not gaps in the wiki. The $500M program of 2024-02-26,
    the 97.5% voting-power figure and the Accel total of 14,514,196 are all in the wiki, mostly in
    `metrics/` files, which the ask prompt never mentions. The prompt says "stop as soon as the question
    is answered".

- The owner chose to fix the prompt and run three evals (commit 033d74f). The wiki-mode prompt now
  greps `metrics/` for figures, answers every part of the question, and gives each figure its total,
  program and later changes. Both and sources prompts are unchanged.
- Three evals (reports 20261109T014427Z, T014736Z, T015056Z; spent **$1.14**):

  | mode | run 1 | run 2 | run 3 | pooled | median cost (pooled) |
  | --- | --- | --- | --- | --- | --- |
  | wiki | 88% | 93% | 83% | **87.8% (108/123)** | $0.0034 |
  | sources | 90% | 80% | 83% | 84.6% (104/123) | $0.0027 |
  | both | 85% | 88% | 90% | 87.8% (108/123) | $0.0036 |

  Every answer was graded and the trap was refused in every mode in every run. 7 answers were asked
  again for bad citations. One both-mode trap answer in run 1 still cited `_schema.md` after its retry;
  no other answer cited an id outside the manifest.
- The cost criterion fails in every run: the wiki-mode median is $0.0007 above sources mode. Both modes
  run on Haiku. On a sample of three questions, wiki mode read fewer tokens in total but wrote more to the
  prompt cache (41.7k vs 31.2k tokens; a cache write costs 12.5 times a cache read). Sources mode greps
  converted text files directly, so the corpus is cheap to search without the wiki. The gap is small but
  consistent, so it is a finding, not noise.

- The owner chose a cost fix and approved changing SPEC's reading order for wiki mode (commit 3e79506).
  Wiki mode now searches first: a Grep of the fact lines with head_limit 30. It reads 40 lines of
  `_index.md` or an entity's Current picture only when the search finds nothing, or when the question asks
  for an entity's current state.
- Three evals with that prompt (reports 20261109T022002Z, T022332Z, T022718Z; spent **$1.12**):

  | mode | run 1 | run 2 | run 3 | pooled | median cost (pooled) |
  | --- | --- | --- | --- | --- | --- |
  | wiki | 85% | 76% | 73% | **78.0% (96/123)** | **$0.0025** |
  | sources | 83% | 88% | 88% | 86.2% (106/123) | $0.0031 |
  | both | 85% | 80% | 80% | 82.1% (101/123) | $0.0038 |

  Cost is now met: wiki answers are cheaper in all three runs and take a median of 5 turns, against 8.5
  for sources. No cited id is outside the manifest (6 wiki answers were asked again), and the trap was
  refused every time. The score fell below 80%. The loss is almost all in two questions: held-fees went
  from 11/12 to 6/12 and held-ga-accel from 11/15 to 5/15. A name such as "Centerview" matches more than
  200 lines. In the held-fees transcript, entity pages, `_index.md` and `escalations.md` filled most of each
  30-hit result, and none of the three searches returned the fee line. So the agent concluded "not in the wiki" for facts that are in `notes/`. For example,
  Centerview's ~$45M fee is in notes/defm14a-merger-proxy-part2.md.

Acceptance status: tests ✓, build ✓, check 0 errors ✓, wiki 93% of corpus ✓, trap refused ✓. The other
three depend on the prompt. The previous prompt meets wiki ≥ 80% (87.8%) but not the cost criterion, and
leaves one stray citation in 90 answers. The grep-first prompt meets cost and citations (0 of 90) but
scores 78.0%. Awaiting the owner. The grep-first prompt is the one committed.

Cost so far: $14.46 (allowance $28). Total spent so far: $16.00 of $50.

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
  subsidiary, Permira funds) because the notebook names no entities for it. `examples/sec-deal/quickstart-tier.toml` is drafted from the quickstart-tier documents (5 lookups, 2
  cross-document, 1 trap; every quoted phrase checked word for word against its unit's text). Still to do: owner approval
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

Cost: $0.00 (M2's paid build is recorded in its own entry above).
