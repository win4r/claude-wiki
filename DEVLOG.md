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

## M5 Acceptance build — done; every criterion met except wiki cost < sources (a tie) (2026-11-09)

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

- The owner approved one more round (commit 11bc27e). Every wiki-mode Grep is now limited to the fact
  folders (glob `{notes,metrics,relations,addenda,contracts}/*.md`). The agent counts hits per file
  first, then searches the top files with a narrower pattern, and narrows any search that hits its limit
  before concluding that something is missing.
- Three evals (reports 20261109T023435Z, T023735Z, T024247Z; spent **$1.15**):

  | mode | run 1 | run 2 | run 3 | pooled | median cost (pooled) |
  | --- | --- | --- | --- | --- | --- |
  | wiki | 83% | 83% | 83% | **82.9% (102/123)** | $0.0027 |
  | sources | 85% | 93% | 90% | 89.4% (110/123) | $0.0024 |
  | both | 80% | 88% | 73% | 80.5% (99/123) | $0.0047 |

  held-fees recovered to 9/12 and held-ga-accel to 9/15. No cited id is outside the manifest (one wiki
  answer was asked again), and the trap was refused every time. Wiki answers take a median of 5 turns,
  against 7 for sources.
- Cost is a tie within the noise, not a win. Sources mode's prompt has not changed since the first eval,
  yet its per-run median has ranged from $0.0023 to $0.0040 over 11 evals (pooled $0.0027 over 110
  answers). Wiki mode's median was $0.0027 here, against $0.0024 for sources in the same runs. Wiki was
  cheaper in run 3 only, and in 13 of 30 paired answers.

Acceptance status (fact-folder prompt, committed): tests ✓, build ✓, check 0 errors ✓, wiki 93% of
corpus ✓, wiki ≥ 80% ✓ (83% in each run), trap refused in every mode ✓, every cited unit in the manifest ✓
(0 of 90). Wiki median cost < sources median: ✗ in the same-run comparison ($0.0027 vs $0.0024), and equal
to sources mode's 11-eval median. Every SEC filing here is already plain text that Grep searches well, so
on this corpus the wiki does not make answers cheaper. Wiki mode takes fewer turns but scores below sources mode (82.9% vs 89.4%).

Closed by the owner on 2026-11-09, with the fact-folder prompt and the cost criterion recorded as a tie.
Changes from SPEC that the owner approved in M5: entity pages hold a `## Sources` table instead of copied
fact lines, and wiki-mode asks search first instead of following SPEC's reading order. SPEC.md itself
is unchanged. Open question for the owner, from SPEC: `both` is still the default mode. Over the last
three evals it scored 80.5% at the highest cost, so `sources` or `wiki` may be the better default.
M6 starts when the owner supplies documents.

Cost: $15.61 (allowance $28): build $11.44 and 11 evals $4.17. Total spent so far: $17.15 of $50.

## After M5: memory-components audit fixes (2026-11-09)

The owner asked for an audit of the tool against Anthropic's "The components of agent memory" (2026-11-06).
It rated 34 components: 4 implemented, 27 partial and 3 missing, 13 of them by SPEC's design. It also found
8 defects. The owner approved fixing the defects that need no paid run, and logging the rest as proposals.
Four adversarial review rounds followed; in the first three, each finding had a refuter:
- Round 1 raised 26 distinct findings. 22 held up and are fixed, refuters rejected 3, and 1 was the reviewer's
  note on `[[accel]]`, which moved two lines (below).
- Round 2 confirmed all 22 fixes and raised 18 more, all confirmed. One was medium: a regression of the
  compile-resume fix. The other 17 were low: edge cases in the eval's git use, the retry note and lost_lines'
  recursion, the template's wording, and 8 guards no test pinned. All are fixed. Each new test was checked
  against the mutation it targets, and all 21 mutants were caught.
- Round 3 confirmed all 18 round-2 fixes and raised 20 more findings; refuters rejected 2. Two of the 18 that
  held were medium, and both came from round 2's own fixes:
  - lost_lines' pair cache kept every pair it compared: 545 MB on a 100 KB page pair of lines that differ only
    in their dates.
  - The eval's pre-check used `git check-ignore`, which reads names as globs, so an ignored question file
    named like `private-[1].toml` passed when a tracked file matched the glob.
  The 16 low ones were edge cases in the retry note, the eval and the manifest's temporary file, plus 8 test
  gaps. Round 4 fixed both mediums, 13 of the lows, and part of 2 more. The rest need a design choice and are
  proposals below.
- Round 4 was to be the last, so only its own regressions were to be fixed. It checked its fixes with one
  verifier per area, on the changed lines only. A refuter would have taken any medium or high regression of
  round 4's own; there was none. It raised 7 findings:
  - Medium, from before round 4: the fixture gave a test the owner's key, PATH and claude on the live marker
    alone. SPEC.md:328 says live tests are skipped unless WIKI_LIVE=1, but only test_live_smoke carried that
    skip, so a live test written without one would have spent money under plain `pytest`. Fixed anyway, since
    it guards money: the fixture now skips every live test unless WIKI_LIVE=1. A live test with no skipif of
    its own fails the suite if that skip is ever lost.
  - Low, from round 4 and fixed: a retry note that fit whole was cut where round 3 told it whole, and at a tiny
    limit the bare count came out one short. The memory test failed when the harness already traced memory.
    A lost_lines comment claimed one byte per pair compared, but each row is as wide as its group. run_eval's
    own mode check had no test.
  - Low, a proposal: no offline test pins what the live branch restores.
  After the fixes, each of 50 mutants, aimed at round 4's changes and at the guards before them, was caught by
  the test written for it.

What changed in the tool:
- **Links resolve through `_aliases.md`** (defects 6 and 7). `grammar.LinkResolver` maps a link in a unit's
  per-unit file to the page its heading's facts are on. It tries the unit's own heading first (a
  `<unit>#<heading>` row, or a bare row when the unit files facts under that heading), then a slug, then a
  heading proposed bare; a heading two rows claim stays unresolved. Compile attaches each relations line to
  every slug its links reach, and the Sources cell names the links used (``lines naming `[[klarna-bank]]` ``).
  `wiki check` counts these links in one note instead of warning. Heading links on README, escalations or
  entity pages still warn, because readers cannot follow them there.
- **Current pictures hold list items only** (defect 1). When compile renders a page, and again after each
  Compile run, `compile.normalize_picture` points every picture link at a slug. A link resolves in the units
  the line cites, and links inside code spans are left alone. It also drops untagged plain lines; a plain line
  with a source tag stays, for `wiki check` to report. The Compile validator rejects a plain line, and `wiki
  check` warns on plain lines and on pictures over 30 lines. A rejected picture names every problem it has.
  Compile's prompt and steering item 6(b) no longer ask for a sections line; the template `_schema.md` for new
  workspaces says the same. README rule 3 and the workspace CLAUDE.md template point readers to the Sources
  table.
- **A filed fact line can't be reworded or removed** (defect 2). `check.lost_lines`, which `check --against`
  now shares, compares every filed line of a changed per-unit file with the task worktree's HEAD. It pairs
  identical lines first and then the rest by a maximum matching, so moving lines never counts as losing one.
  The matching works on distinct line texts and has no recursion, so a file with a thousand identical lines
  takes milliseconds; the first version raised RecursionError there. For each base text a search reaches, it
  keeps one byte per current text of the same group, recording what comparing the two gave. It remembers a
  text whose search failed, since no later pairing can open a path for it. On 100 KB page pairs, 1,062 copies
  of a dropped line take 0.07 s, where round 3 took 52 s, and 1,818 lines that differ only in their dates peak
  under 30 MB, where round 3 used 545 MB. It gives the same results as the first version on 12,000 random inputs,
  and the suite checks it against a brute-force matching on 1,800 more.
  `lifecycle_only_added` now guards every tag on a line, a CONFLICT line's first tag included. The Check,
  Resolve and dream-apply validators reject a run that rewords, moves to another unit or removes a filed
  line.
  - Check's rule 4 no longer invites "fixing" a tag the program accepted: in the core build, Check runs had
    rewritten `section: 7-Indebtedness` and dropped `section: not numbered in this document`.
  - The hedge rule now says to retract the line and file the fact as the unit states it in addenda. It used to
    say "reword in place", which contradicted rule 4.
  - Resolve's and dream apply's prompts now state the rule too.
  - Replayed on the core build's history, the guard would have rejected the Check runs for 7 units: 36 lines,
    24 of them rewordings and 12 contract tags. It would have rejected none of Resolve's edits.
- **A retry hears every reason it was rejected.** The retry note used to be the first 3 problems, so a run
  rejected for changed lines and for a bad tag heard only about the lines. It now takes the first problem of
  each kind before any repeat. A quoted line holding both quote kinds, or a `[[link]]`, no longer makes a kind
  of its own. A list that fits the 2000-character note is told whole. Otherwise problems are told in order,
  and one too long for the room left is passed over for the ones after it. When none fits whole, the first is
  cut so the "(N more of these)" count stays whole.
- **A resumed compile reruns the group that failed** (pre-existing bug). Groups were numbered from compile-01
  on every entry to phase 7, so on resume a failed group could take the id of a group that had finished. It
  was then skipped as "done earlier", and its new facts_sha recorded with a stale picture. The groups are now
  kept in state.json. Each group also records its pages' facts_sha and normalizes their pictures as soon as it
  merges. So a build stopped hard later in the phase (Ctrl-C, or a git error in a worker) does not pay for the
  finished groups again on resume. manifest.json is now written through a temporary file, so a stop mid-write
  cannot leave it unreadable, and a failed write removes the temporary file.
- **Retracted red flags leave the index catalog** (part of defect 5), and the escalations rules say so.
- **Dream transcripts show what the reader did:**
  - each tool call, with its arguments and the first 8 lines of its result;
  - a `flags:` line covering refused, citation retry (including a retry that failed), unknown units,
    malformed tags and a non-success outcome;
  - after a successful citation retry, the rejected first attempt.
  Streams are split on newlines only, so a U+2028 in a document no longer drops an event. Ask records keep both
  runs' token usage, the first answer and its stream, or the failed retry's outcome and stream.
- **Eval reports** add a score per question kind, median tokens per answer, and the commit the answers read
  (`summary.wiki_commit`). For that claim to hold, `wiki eval` now refuses to start while `repo/wiki` or
  `manifest.json` has uncommitted changes or a build is unfinished. Untracked files count whatever
  status.showUntrackedFiles says. A sources-only eval reads no wiki, so it runs anyway, and its report names no
  wiki commit.
  - The eval commits only its report files and a question file kept in repo/, and takes their names literally;
    before, it committed every change in the repo.
  - It refuses before any run if git would ignore one of those files. Before, every run was paid for, then the
    commit failed and left the reports staged. The check is the commit's own literal `git add`, dry-run, one
    path at a time.
  - A mistyped, repeated or empty `--modes` is refused before the estimate. Before, the valid modes' runs were
    paid for and the eval then failed.
  - Dreams' clean-repo check counts untracked files the same way. Under git's default settings, nothing changes
    for them.
- **Offline tests cannot reach the real claude.** At import, tests/conftest.py takes the owner's key out of the
  environment, and points WIKI_CLAUDE_BIN and the front of PATH at a stub that refuses. A test that undoes its
  fixtures therefore falls back to the stub. A live test is skipped unless WIKI_LIVE=1, whatever skipif it
  carries, and only then gets the owner's values back (incident below).
- **Streams survive a rerun** (defect 8). `runner.run` renames an earlier run's `.jsonl`, `.stderr`,
  `.cmd.json` and `.violations.txt` to `<task>.prevN.*`. The audit suggested appending instead, but one file
  per run keeps each stream a single session, as the cost and transcript readers assume.

Changes from SPEC, with SPEC.md unchanged:
- SPEC.md:92 says links are bare slugs. The extractors link the headings they propose, so the program
  resolves those links through `_aliases.md` instead of rewriting per-unit files. Agents still may not
  repoint links (SPEC.md:246). Only Current pictures, which the program owns, are rewritten to slugs.
- Pictures hold list items only, and steering item 6(b) (SPEC.md:254) no longer asks for a last plain line.
  That line followed from M5's Sources tables. The sha-pinned `_schema.md` in existing workspaces still allows
  it, so compile.md says pictures hold list items "even where an older _schema.md allows a last plain line".
- The fact-line guard runs per writer task, before merge, rather than as a build-level `check --against`.
  Re-extraction legitimately replaces a changed unit's files (SPEC.md:188), so a build-level comparison would
  reject correct builds.

Core workspace, offline, in two commits of its repo, 8cb355a and 8fb2824 (owner-approved, no paid runs):
- The program re-rendered all 296 entity pages:
  - 296 navigation lines dropped;
  - 26 picture links repointed to slugs;
  - 119 relations lines newly attached across 42 pages (the audit counted 114 alias-named lines; a line can
    reach two pages);
  - two `[[accel]]` lines in 8k-amended-agreement moved from accel to accel-leaders-3, because that unit's
    `## accel` is Accel Leaders 3.
- manifest.json is untouched, so 44 slugs keep their old facts_sha and get a fresh picture at the next paid
  compile. That is 5 Compile groups, about $0.13 at the ledger's mean compile run ($0.026), and at most about
  $1.10. No estimate shows it yet.
- README.md rules 3 and 9, and step 3 of the workspace's CLAUDE.md, were edited by hand.
- `wiki check`: 0 errors and 0 warnings (was 173). 26 notes, among them 147 links that resolve through
  aliases.
- The quickstart workspace is unchanged. Under the new rule its `wiki check` shows 46 picture warnings, one
  per sections line; its next build drops those lines.

Left as found:
- escalations.md still ranks the 7 retracted red flags until the next Index run. It is an agent-owned page,
  so I left it unedited by hand.
- The audit found 3 pictures that cite retracted lines.
- metrics/8k-closing.md:7 keeps the wrong basis for the offer expiry, with only a correcting line after it.
  A fix touches one line, so the wrong line stays.
- The 24 lines that Check reworded in the core build stay reworded; the guard applies from the next build.
- 53 lines state $44.00 and none has `valid_to` (defect 3).
- Re-extraction still deletes a changed unit's lines (defect 4).
- relations/8k-amended-agreement.md links `[[accel]]` twice for Accel Leaders 3. Compile files those lines on
  accel-leaders-3, but a reader who follows the link lands on accel. Agents own the per-unit files and links
  there are never repointed (SPEC.md:246). The a-r-merger-agreement picture keeps one such link, because on a
  page a slug stays a slug. The next refresh of accel-leaders-3's picture may copy it too.
- Normalizing a picture drops the untagged continuation of a wrapped item. The core pictures had none: all 296
  dropped lines were navigation lines. The Compile validator rejects wrapped items.

Incident: a round-3 refuter's scratch test called monkeypatch.undo(), which also undid the fake-claude fixture,
so its 7 resumed builds ran the real claude 14 times with the owner's API key. They ran with the runner's
restricted flags and a private config dir. By the cost the runs reported, they spent about $0.04 to $0.11, in
no ledger: theirs were in pytest temp dirs, since deleted. Both workspace ledgers are unchanged, last written on
2026-11-08, and no key appears in any file under the project or the scratchpad. The suite's new guard (above)
closes the hole. Round 4's checks ran with a fake key and with a canary as the only claude on PATH. The canary
logged just 3 `--version` calls, which one reviewer made to it on purpose, and both ledgers kept their mtime
and size throughout.

Proposals, grouped by what each needs:
- A SPEC change:
  - LESSON, PATTERN, SUPERSEDED and PITFALL finding kinds for dream analysts, and a small lessons page
    that dreams own (SPEC.md:289-290). Today every finding becomes a fact fix.
  - Settle SPEC.md:96 against :188. Keep a changed unit's old lines with `valid_to` instead of deleting them
    on re-extraction.
  - A verified marker on fact lines, set by Check, and a policy that ask and compile rely on verified lines.
  - Preload the "Where to start" map into the ask prompt, instead of the reading order at SPEC.md:272.
  - An owner feedback command whose notes feed dreaming alongside ask transcripts.
  - Alias merges, and lifecycle marks that can be reversed. Today `_aliases.md` is append-only, and
    valid_to and retracted are one-way.
  - A path for the 563 open questions that no document answers, such as a backlog page or an UNRESOLVABLE
    sweep.
  - Mark supersession across documents, such as the 53 $44.00 lines.
  - Guard contract quote text. SPEC guards fact lines only, so a Check run may still change a `>` quote
    while keeping its tag line.
- A paid run:
  - A `wiki tune` loop that reflects on eval failures and proposes prompt edits, scored on held-out
    questions.
  - Prompts conditioned on the model: `render()` takes no model today.
  - A purpose-first ask_wiki variant, evaluated against the current recipe.
  - Teach the ask and compile prompts what `retracted` means. Neither mentions it.
  - Refresh the 44 pictures, and the 3 that cite retracted lines.
- Offline, later:
  - A configurable cap on the Borges ratio that fails a build, with the ratio always reported.
  - Checks for the 40-line caps on `_index.md`'s map and README.md.
  - Dream cadence driven by access counts and by facts likely to change.
  - Growth counters per build.
  - A lint that checks contract quotes against their units.
  - Reject a Compile run that leaves one of its pages' pictures blank. Today such a run counts as done, and the
    page gets a new group on the next build.
  - Refuse an eval while a merge or cherry-pick is unfinished. Today it pays for every run and then cannot
    commit, which a failed `wiki dream --accept` can cause. dream --accept could also run `git merge --abort`
    when its merge fails. Whether to refuse a revert, a paused rebase or unmerged entries too is the open
    choice.
  - Word the ignored-file refusal by what is blocked. A rule on the reports needs a negation such as
    `!eval/report-*`; only a blocked question file can move outside repo/. Refuse up front, in plain words,
    when repo/eval is a symbolic link, instead of passing git's error on.
  - Remove a stale manifest.json.tmp. A kill during the write can still leave one for the next commit. Where to
    remove it (when the lock is taken, when a build starts, or in commit_all) is the choice; build start alone
    misses `wiki add`.
  - Stop starting retries once a build is interrupted. After Ctrl-C, a worker still started and paid for its
    run's retry.
  - lost_lines still takes about 9 s on an adversarial 100 KB page pair whose 1,818 lines share one fact text
    and differ only in their dates, since it compares every pair once. An index by lifecycle values would
    cut that. Its rows are as wide as a group even when a search compares one pair in them: 2.25 MB for 2,999
    comparisons on a 1,500-line page. A row could stay a small dict until it fills.
  - An offline test of what the live branch restores, such as a pytester run with a fake owner key. Today only
    a live run checks it; a broken restore fails safe, at the stub.
  - test_run_failures[badtools] failed when three suites ran at once. Its fake run sleeps 5 s for the runner to
    stop it; when the stop lands later, the run finishes and is charged its reported cost, not the full cap.
    The charge is right either way, but the test's expectation depends on timing.
  - retry_reason's kind() strips `\S+\.md` from each problem, which takes quadratic time on a long run of
    characters with no spaces.

Tests: 151 (149 passed, 2 live tests skipped). Cost: $0.00 by the ledgers, plus about $0.04 to $0.11
unledgered (the incident above). Total spent so far: $17.15 of $50 by the ledgers; at most $17.26 in all.

## After M5: whole-project review (2026-11-10)

The owner asked for a review of the whole project, with bugs and optimizations fixed without waiting for
approval. I read every source module against SPEC.md. Three reviewers each read one group of modules on a copy
of their own, offline, with a fake key and a canary in place of claude. They reported 32 findings: 24 open, and
8 that I had fixed by then. All 24 are fixed, one of them in part (below), along with 4 points from their
unverified lists and what my own reading found. Every finding was reproduced through the real code path before
it was fixed, by me or by the reviewer who reported it. No paid run was made.

Stopping a command:
- **Ctrl-C stops a command, and nothing more is paid for.** `runner.run_all` now runs every pool of runs (build
  phases, eval answers, dream analysts). On Ctrl-C it stops the running runs (SIGINT, then SIGTERM), starts none
  of the waiting ones, and a stopped run is never retried. Before:
  - A build retried each run that Ctrl-C cut short, and did not come back to the prompt until the retries ended.
  - An eval started a grader for each answer that was cut short, and paid for it.
  When one task raises instead (a run changed its input, a git error), no further run starts and the running
  ones finish, so their work is kept.
- **A kill or a closed terminal ends a command the same way.** SIGTERM and SIGHUP used to end `wiki` at once:
  no ledger line for the runs in flight, the runs still going, and the lock left behind.
- **An interrupted run is in the ledger.** `wiki ask` stopped by Ctrl-C left no ledger line for a run that had
  spent money. The run is now stopped and recorded as `interrupted`, charged its full cap when it sent no
  result, as SPEC says of unknown spend.

Dreams:
- **`--accept` merges only what it checked.** It checked the files in the dream's worktree and merged the branch
  tip. A tip that failed `check --against` could be merged by restoring the file without committing, and an
  edit that was not committed was checked, left out of the merge, and deleted with the worktree. Accept now
  refuses unless the worktree is at the branch tip with nothing uncommitted.
- **`--accept` leaves main as it was when the merge fails.** When main had changed since the dream started (a
  build, say), accept removed the dream's worktree, then left repo/ in the middle of a merge with conflict
  markers in manifest.json. It now merges first, aborts a merge that fails, and says to discard the dream.
- **A dream that was cut short can be discarded.** Its status stayed `running`, so `--discard` said no dream was
  open, and its branch and worktree stayed.
- **A failed analyst fails the dream.** It was read as "no findings": the dream passed, and accepting it used up
  transcripts no analyst had read.
- **A failed Link run stops the dream.** It went on to pay for Compile and Index, and only then failed its check.
- **The estimate and the budgets follow the ledger** (SPEC.md:314). The estimate stayed at $1 plus $0.15 a
  transcript, so 14 waiting transcripts stopped at the $3 cap, and Index got a fixed share whatever Index had
  cost. `cost.dream_task_estimates` now gives each run its ledger average, or its starting figure.
- **A dream splits a long escalations.md as a build does.** Without that, no dream on the core wiki could pass
  its check: its page needs splitting.

Documents:
- **A document added again unchanged on a later day stays unchanged.** A document with no [SOURCE] line of its
  own gets `added: <today>` in its text, so on a later day it counted as changed and the next build paid to
  extract and check it again. It now keeps the day it has while its text is the same. The example filings carry
  their own line, so M5 never met this; the owner's documents in M6 would have.
- **`wiki add` refuses before it writes.** One file it could not convert, or two files making one slug, stopped
  it with the earlier documents already replaced in docs/ and the manifest not saved.
- **Two files of one name in different folders are refused.** The second silently replaced the first.
- **A document added again before `--resume` is extracted again.** The resumed build took its finished tasks
  from state.json and filed the corrected document under its new sha256 with the old facts. Still open: a
  document that shrinks to fewer units before the resume has its new unit wait for the next build.
- **A document split as before again is live again.** After shrinking to one unit and growing back, every unit
  stayed `removed`: no build filed it and the index left it out.
- **Conversion:** UTF-16 text became its letters with a NUL between each; HTML whose `<head>` is never closed
  became empty; a damaged .docx, .xlsx, .pptx or .pdf, or an .eml in a charset Python does not know, ended
  `wiki add` in a traceback. Files under a dot-folder are no longer added.

Build and check:
- **Errors are caught at the run that makes them.** Three kinds passed their run's checks and failed the check
  that ends the build, after every later phase was paid for, and a resume then had nothing left to run:
  - Link giving a slug a second row;
  - a per-unit file over 100 KB;
  - an escalations.md over 100 KB with no `### ` entry to split at. The Index prompt now names that heading.
- **A Compile run may not leave a picture empty** (a proposal of the last entry). Such a run counted as done,
  and the next build paid for its pages again. compile.md now asks for at least one line.
- **Old escalations pages are not appended to a page Index wrote whole.** On the build after a split, the
  continuation pages were added to the new page: on a copy of the core wiki, a page written whole with its 372
  entries came out as 640.
- **`wiki check` keeps tags to their fields:** a `section` outside contracts/ and a date that is no date are
  errors, and tag errors give the file's line number, as the other rules do.
- **A lifecycle mark counts only inside a tag.** `| retracted: <date>` added to a line's text passed
  `check --against` and the writers' guards as if it were a mark.
- **A slug that ends like a fact page's name** (`key-facts-2024`) is an entity page; it failed the final check.
- **`wiki build --resume` says what it may spend** and takes `--max-usd`; it printed no figure and ignored it.

Runner, eval and ask:
- A run with no system/init event failed open: it passed as a success though nothing showed its tools. It is now
  `bad_tools`. A plugin listed by name crashed the check.
- A result whose cost cannot be read is charged its cap; it used to raise before the ledger line.
- A steering file that wiki.toml names but that is missing stops a paid command; it was read as empty.
- A relative `api_key_settings` is taken from the workspace folder; a run's working directory is a worktree.
- `wiki eval` refuses while git is in the middle of a merge, cherry-pick, revert or rebase (a proposal of the
  last entry): every run was paid for and the report then not committed. It also refuses a question id that
  cannot name a file, and criteria given as one string, of which each character was graded.
- The eval report's break-even column says `-` without a sources baseline, where it said "never".
- Ids in a malformed source tag are compared with the manifest too: a second id in one tag, or one with a
  capital or a space, was never reported. On the 354 stored eval answers of both workspaces, no answer's verdict
  changes.
- `wiki smoke` keeps its budgets within its cap, `--max-usd` and what is left of the total; they were $0.70
  whatever those said.
- Two commands in one second shared a run id, and the second wrote into the first's folder. A command now waits
  for an id of its own.
- Smaller: a manifest.json.tmp left by a killed write goes when the lock is taken; a `batch_chars` too small to
  hold a part's [SOURCE] line is refused, where splitting looped for ever; the runner reads a stream as UTF-8
  whatever the locale; `claude-fable-5-2` has its own starting prices; two unused functions left the runner.

Faster, with the same results:
- `wiki check --against` read the base's copy of every per-unit file with one git process each. It now compares
  blob ids first and reads only files that changed: 1.88 s to 0.13 s on the core wiki.
- Compile parsed a per-unit file again for every heading in it: 0.28 s to 0.04 s on the core wiki.
- `retry_reason` took quadratic time on a long run of characters without spaces (a proposal of the last entry):
  9 s on 60,000 characters, now under a millisecond, with the same results on 300,000 random strings.
- On copies of both real wikis, `wiki check`, `check --against` three base commits, and Compile's page groups
  are identical before and after this entry's changes.

Changes a paid run has not yet exercised:
- Two prompts changed by a clause each: compile.md ("at least one line") and the escalations rules in the Index
  prompt (the `### <rank>. <title>` heading, which M5's Index used unasked).
- Stricter run checks (size, tag fields, an empty picture, a second row) can reject a run that used to pass. A
  rejected run is retried once and told why.
- Whether the real Index rewrites a split escalations.md whole, and what a dream's Index costs on the core
  wiki, need a paid run to see.

Tests:
- 196 (194 passed, 2 live tests skipped), up from 151; the suite passed twice in a row. Each of 74 mutants, one
  per fix or guard of this entry, was caught by the test written for it.
- Tests that press Ctrl-C send the test process a real SIGINT (`conftest.interrupted`).
- The fake claude's `badtools` run sleeps 60 s, not 5, before the runner stops it; on a busy machine the 5 s
  ended first (a proposal of the last entry).
- The fixture's live branch is checked offline (a proposal of the last entry): the live check runs in a pytest
  of its own with a made-up key and no claude on PATH.
- No run reached the real claude: the canary logged no call, and both workspace ledgers are unchanged.

Proposals:
- Refuse `wiki build` and `wiki add` while a dream is open for review, or rebase the dream. A build on main
  makes the dream unmergeable (both change manifest.json), and the dream's cost is then lost.
- A way to finish a build whose final check fails: every phase is done by then, so a resume runs nothing.
- Take a document that shrinks before a resume into the resumed build, or refuse that add.
- Hold the lock with `flock`. Two commands that meet a dead holder's lock at the same moment can both enter;
  shown only with a delay injected.
- The cap is checked, then the run made, per worker thread, so parallel runs can pass a cap together by up to
  their budgets. The 10% held back covers the usual case.
- Splitting counts characters and batching counts bytes, so units of CJK text come out about three times the
  planned size.
- facts_sha covers a row's description, so a Link run that rewords one would trigger paid Compile runs.
- `wiki check` hashes _schema.md after newline translation; a change of line endings alone passes it while the
  build's own check stops.
- `wiki smoke` exits 0 when its checks fail.

Cost: $0.00. Total spent so far: $17.15 of $50 by the ledgers; at most $17.26 in all.

## After M5: benchmark runs and what they changed in `wiki ask` (2026-11-10)

The owner asked me to run the benchmarks, fix what scores poorly, rerun, and repeat until the gains level off,
with paid runs allowed without asking while this task's spend stays under $50. I kept to the tighter limit that
was already in place: the core workspace's `caps.total_usd` of $48.46, which only the owner raises. Every paid
run went through `wiki eval`. The task spent **$11.75** (2,067 runs, all `success`).

Result: three runs of each set before, and two measurements of three runs each after, all modes (criteria
met; median cost per answer). The second measurement followed the last two program fixes of point 5 below,
which cannot move a score by much, so the two are best read as repeats of one measurement.

| set | mode | before | after, 1st | after, 2nd |
| --- | --- | --- | --- | --- |
| core-tier (10 questions, 41 criteria) | wiki | 93/123 = 75.6%, $0.0026 | 107/123 = 87.0%, $0.0032 | 114/123 = 92.7%, $0.0030 |
| | both | 108/123 = 87.8%, $0.0038 | 112/123 = 91.1%, $0.0034 | 109/123 = 88.6%, $0.0035 |
| | sources (prompt unchanged) | 106/123 = 86.2%, $0.0025 | 106/123 = 86.2%, $0.0027 | 107/123 = 87.0%, $0.0024 |
| quickstart-tier (8 questions, 30 criteria) | wiki | 76/90 = 84.4%, $0.0015 | 84/90 = 93.3%, $0.0018 | 83/90 = 92.2%, $0.0019 |
| | both | 81/90 = 90.0%, $0.0022 | 84/90 = 93.3%, $0.0020 | 82/90 = 91.1%, $0.0021 |
| | sources (prompt unchanged) | 77/90 = 85.6%, $0.0014 | 82/90 = 91.1%, $0.0015 | 82/90 = 91.1%, $0.0015 |
| second set (12 questions, 49 criteria) | wiki | 133/147 = 90.5%, $0.0027 | 140/147 = 95.2%, $0.0032 | 138/147 = 93.9%, $0.0037 |
| | both | 140/147 = 95.2%, $0.0036 | 139/147 = 94.6%, $0.0038 | 140/147 = 95.2%, $0.0039 |
| | sources (prompt unchanged) | not run | 137/147 = 93.2%, $0.0029 | 132/147 = 89.8%, $0.0028 |

How to read it:
- **Wiki mode is better, by more than the noise.** Over the three sets it went from 302/360 (83.9%) to 331/360
  (91.9%) and 335/360 (93.1%): about 8 to 9 points.
- **On core-tier alone the gain is about 10 points, not 17.** The noise there is large: the unchanged wiki
  prompt scored 82.9% on 11-09 and 75.6% today, and the two measurements after the changes gave 87.0% and 92.7%.
  Pooled, that is 195/246 (79.3%) before and 221/246 (89.8%) after. The unchanged sources prompt moved 5.5
  points on quickstart-tier between today's measurements.
- **Wiki mode now costs more than sources mode.** Its median answer cost rose by $0.0003 to $0.0010 and is 10% to
  32% above sources mode in the same runs (25% to 32% in the second measurement), where M5 recorded a tie. The
  extra tokens are the map and the page reads that the points came from (on core-tier, cache writes per answer
  rose from 11,200 to 14,200 tokens; the mean cost rose 9%, from $0.0030 to $0.0033). The trade is the owner's
  to judge; I did not spend iterations on cost.
- **Both mode is unchanged in accuracy** (329/360 before; 335/360 and 331/360 after), in fewer turns (core 8.5
  to 6, quickstart 7 to 5) and at about the same cost.
- **In both measurements after the changes every trap was refused in every mode, and no answer cited an id
  outside the manifest.**
- Reports, in each workspace's `repo/eval/`, all dated 20261110:
  - core-tier: before T150735Z, T151142Z, T151607Z; after T162603Z, T162958Z, T163414Z and T165825Z, T170223Z,
    T170724Z.
  - quickstart-tier: before T150735Z, T150929Z, T151143Z; after T162603Z, T162849Z, T163138Z and T165825Z,
    T170115Z, T170315Z.
  - second set (`report-heldout-tier-*`): before T154733Z, T155207Z, T155418Z, run through the same CLI from a
    checkout of eaaac79, wiki and both modes only; after T163810Z, T164041Z, T164440Z and T171053Z, T171342Z,
    T171734Z.
  - The runs in between (one mode each, and a run of the second set before the first measurement) are there
    too.

What scored poorly, and why:
- Wiki mode lost its points on facts the wiki holds. The saved runs show where each search went wrong:
  - **A fact filed under another name.** The fee and "no fairness opinion" lines for J.P. Morgan are filed as
    "JPM". `_aliases.md` maps the two, and the run never read it.
  - **A later document not searched.** Answers gave the $44.00 price as final, or called the closing 8-K's
    $46.50 an unexplained conflict: nothing told the run which units were the amendment and the closing.
  - **A search cut off at its limit.** A name such as "Parent" matches hundreds of long lines, and 30 of them
    rarely held the line needed (the lenders, the $2.1B term loan at closing).
  - The entity pages answer several of these in their Current picture, and the M5 prompt read a page only when a
    search found nothing.
- The trap's first criterion failed in wiki mode in 2 of 3 runs: the answer began "Not in the wiki" and then
  quoted the 2025 projection of $1,374M.

Changes, in the order tried (wiki mode on core-tier, three runs each: 75.6% → 87.0% → 90.2% → 91.1%, then the
two measurements above at 87.0% and 92.7%):
1. **The program hands the run a map of the wiki** (`ask.wiki_map`), in wiki and both modes, after the question:
   - the index's "Where to start" section;
   - for each entity the question names, its page and the other names `_aliases.md` gives it (the longest name
     wins where two overlap, a two-letter acronym matches only as written, a row with no page is left out);
   - the units with their index descriptions, oldest first by the date in each document's [SOURCE] line. A
     document with no such date is listed last, and the list then says so (added after the measurements; every
     unit of both example wikis is dated, so their tasks are the same text as measured).
   The wiki prompt reads the Current picture of one to three pages, then searches the fact folders under every
   name. 75.6% → 87.0%.
2. **Search the latest units for anything that can change**, and every Grep names `output_mode "content"` (runs
   had been spending turns on file lists). 90.2%.
3. **Figures from `metrics/` first; each part of the answer led by where things stand at the latest date; the
   amount where the question asks what someone owns, owes, pays or commits; no projection quoted in place of a
   reported figure the wiki lacks.** 91.1%. The +0.9 is inside the noise, so I stopped changing the wiki prompt.
4. **The both prompt follows the same plan**, with the documents kept for gaps and for confirming a figure. Its
   old plan sent runs to `<slug>.md` at the wiki root, where no page is (3 failed reads and a Glob in one traced
   run).
5. Program fixes that the runs exposed:
   - **A tag that names a unit's file is repaired, not asked again.** `[source: metrics/8k-closing.md | …]` can
     only mean the unit `8k-closing`. In the first measurement 24 of 270 answers were asked again, 15 of them
     for this alone, and each retry is a second paid run. In the second, 12 were (all cite an entity page's
     slug; 11 are in both mode), and 7 tags were repaired.
   - **A tag inside a Markdown table**, with its bar escaped (`\|`), was read as an id outside the manifest.
   - **A grade with a comma before the closing bracket was lost** (`[true, true, false, ]`): the answer counted
     as ungraded, 0 of 3. One answer in the baseline and one in the first measurement were lost this way; the
     committed reports stay as they were run.

The second question set (`examples/sec-deal/heldout-tier.toml`, evidence beside it):
- A subagent wrote it from the 29 documents alone, without the wiki or any eval result, on topics the core set
  does not ask about. I checked 13 of its figures against the documents.
- The code before this entry already scored 90.5% (wiki) and 95.2% (both) on it, so it had little room to show
  a gain. What it shows is no sign that the changes fit the core questions only: wiki mode moved from 90.5% to
  95.2% and 93.9%, which is inside the noise, and both mode stayed at 94.6% to 95.2%.
- It found the table-tag and file-path bugs above, which the core set never triggered.
- I have read its per-criterion results, so it is no longer held out. Its header says so.

What the owner should know before relying on these numbers:
- The "held" questions of core-tier have been tuned on since M5. No prompt change was made for a
  quickstart-tier question, but the set is not untouched: I read its baseline results, and the last two program
  fixes came partly from its first measurement (the lost grade was a quickstart answer, and so were 7 of the 24
  retries).
- **Reading order.** The search-first order the owner approved in M5 (commits 3e79506, 11bc27e) is replaced:
  wiki mode again starts from the index's map and the entity pages, as SPEC describes, but the program supplies
  the map instead of the run reading `_index.md`. SPEC.md is unchanged and does not describe `wiki_map`.
- **The projection rule is a judgment call.** A run now says a projection exists without quoting its amount
  when the question asks for a reported figure. It follows the trap's criterion ("gives no FY2025 revenue
  figure"), and only that question tests it. An owner who wants the labelled projection quoted can delete one
  sentence from each ask prompt.
- "A later document overrides an earlier one" could mislead where a later document is weaker (the analyst note
  and the vendor extract are dated after filings). I found no case of it:
  - demo-ebitda scored 35/36 in wiki mode and 34/36 in both mode over three measurements (36/36 before). The
    three answers that lost a point all quote the $252M estimate as an unverified analyst figure; they lose the
    point for not tying it to the Credit Agreement definition, which the wiki says the note does not name.
  - qs-vendor-rows scored 29/30 in wiki mode over two measurements. In both mode it fell from 14/15 to 10/15,
    twice: the traced answers are short and leave points out, and none treats vendor data as overriding a
    filing.

Not fixed, and proposals:
- **held-ga-accel stays at about half** (8/15 wiki, 6/15 both, 7/15 sources in the second measurement). Its second criterion wants the
  Exhibit A total of 14,514,196 with the 2.6% that belongs to a different count (14,563,386); it was met in 3 of
  the 18 answers of the two final measurements. I did not tune for it.
- **An entity page's picture cannot see facts filed under another heading.** `squarespace-merger` has no line
  from the amendment or the closing 8-K, whose extractors filed the new price under `a-r-merger-agreement` and
  `squarespace`. Its picture still leads with $44.00. Wiki-mode asks now get past this through the unit list;
  a reader of the page does not. Proposal: let Compile see, per page, the later units that name the entity.
  Compile for all 296 pages cost $0.80 in the M5 build.
- **A citation retry can replace a complete answer with a worse one.** It starts a new session. In one both-mode
  run of ho-termination-fees the first answer cited `squarespace-merger-agreement` once, and the second answer
  lost three criteria. Proposal: grade or keep the first answer when the retry's answer is shorter, or ask only
  for the tags to be corrected.
- Both mode still cites an entity slug in about 1 answer in 8, though the prompt and the map forbid it.
- Output tokens are about 35% of an answer's cost, and much of that is thinking, not the answer (1,892 of 4,432
  tokens in one sampled run). The runner sets no effort level.
- `both` is still the default mode. Over the two measurements it scored 666/720, the same as wiki mode, at a
  higher cost.

Tests: 202 (200 passed, 2 live tests skipped), up from 196: the map (start section, names, shared and
overlapping names, acronyms, unit order and dates, an empty wiki), the task of each mode, the tag repair end to
end with no second run, escaped bars, a trailing comma in a grade, and the prompt rules the map relies on.

Cost: $11.75 (27 evals of 3 modes, 12 of 1 mode, 3 of 2 modes; of it, $6.17 for the two measurements after the
changes). Total spent so far: $28.91 of $50 by the ledgers;
at most $29.02 with the incident of 2026-11-09.

## After M5: review of the wiki map, and its fixes (2026-11-10)

The owner ran a code review of the last entry's commit (8959a75) and asked for its 15 findings to be fixed. All
are in the map that `wiki ask` hands a run, or in the ask prompts. No paid run was made: the leave to make paid
runs without asking belonged to the benchmark task, not to this one. **So nothing below is measured, and the
scores of the last entry describe the code before these changes.** (Measured since, with the owner's leave: see
the next entry.)

What the scores of the last entry still describe. Before changing anything I saved the task and the prompt of
all 30 benchmark questions in wiki and both modes (60 tasks), and compared them afterwards:
- Unchanged: every unit list (60 of 60), every "Where to start" item, and which entity pages each task names
  (56 of the 60 name at least one).
- Changed in every task: the map's first line and its new last line, the "Where to start" heading (it now says
  where `[[escalations]]` is), the "Names in the question" heading, and six passages of each prompt.
- Changed in 84 of the 88 name lines: the other names listed (336 where there were 528), and 8 lines are now
  marked as a maybe ("merger", "parent", "company", "board" written in lower case).
- Moved by the second commit: the start map and the unit list, from the task to the end of the system prompt.

Fixes, in the order of the review:
1. **A document with no date of its own.** The map dated a unit only from `filed`, `as-of` or `date` in its
   [SOURCE] line. A document that `wiki add` gives an `added:` line (every document of M6 that has no [SOURCE]
   line of its own) was undated, and the prompts still said "oldest first". Now:
   - such a unit takes the date most of its fact lines carry, marked `~2024-03-01`, and the list's heading says
     what the mark means. The schema makes a fact's as-of the date the document states it. The add day is left
     out, since that is where an extractor lands when a document states no date. On the 39 units of the two
     example wikis this date is the document's own for 27, and within the document's period for the rest
     (a 10-K's year end, a proxy annex's agreement date);
   - a unit with no date either way is listed last, and when no unit has one the heading says the list is in no
     order of time;
   - the prompts say the list is in the order its heading states, and that where the map dates no unit the
     as-of dates in the tags tell which line is later.
2. **`[[escalations]]` and `[[README]]` are not under `entities/`.** The heading said every `[[slug]]` is
   `entities/<slug>.md`; the first item of the core wiki's map is `[[escalations]]`. The heading now names the
   linked pages that are beside `_index.md`.
3. **An ordinary word matched an entity's name.** "block", "limited", "slack", "box" and "our" in a question
   were reported as Block, Inc., a subsidiary, Slack, Box and Squarespace. The same path gives the useful
   "parent", "board" and "the company", and the program cannot tell the two kinds apart. So:
   - a pronoun or article ("our", "we", "its") never matches;
   - one lowercase word that the wiki writes with a capital is offered as a maybe: `"block", if the question
     means "Block"`. The prompts tell the run to use that page only if the question means the name;
   - when more than eight pages are named, the maybes give way first.
   No benchmark question loses a page by this.
4. **Case variants used up the eight places for other names**, and "the Company" was cut for Squarespace. Left
   out now: a name that holds the words in the question (the same search finds it), a name that differs from
   one listed only in case, punctuation, a possessive or a leading "the", and a name that holds a listed name of
   two or more words. A one-word name stands for no longer one, since a run may find "Agreement" too broad to
   search for. Names beyond eight are counted: "and 4 more in _aliases.md".
5. **Over 80 units the list vanished**, and the prompts no longer named `_index.md`. Now the latest 80 are
   listed and a line says how many more `_index.md` has; the start map takes the 40 lines SPEC allows and says
   when it leaves items out; the prompts say a section may be absent, and to Grep `_index.md` when the map gives
   no page or unit for what the question asks.
6. **The map is written to cache on every answer.** Fixed in a commit of its own, described under "The cache
   change" below: it moves wiki text into the system prompt, so it can be reverted alone.
7. **The projection rule did not say how to refuse.** The prompts told a run to "say the figure is not
   available"; the program counts an answer as a refusal only when it starts "Not in the wiki", as SPEC says a
   miss does. The rule now says to answer that part with "Not in the wiki", as the first words when the question
   asks for nothing else. `refused()` is unchanged, so the count of traps refused means what it did.
8. **The unit list came from the index's prose.** It now comes from the manifest's live units, each with its
   description from `_index.md` when the index has one, in the index's order. An item written `**unit**` or
   `unit. A memo` is read; a unit the index leaves out is listed without a description; a removed unit is not
   listed. Without a manifest the index's items stand.
9. **The citation retry's note came after the map**, under the map's "information, not instructions". The note
   now comes straight after the question, and the map ends with the line "End of the wiki map." that its first
   line names.
10. **The start map was the first section, whatever its title.** It is now the section named "Where to start";
    an item wrapped over several lines is kept whole.
11. **The eight-page limit was applied before rows with no page were dropped.** It is applied after.
12. **Text that came from documents sits in the task.** Mitigated, not removed: each copied item is cut at 500
    characters and cannot write the line that ends the map, and the map is fenced by that line. An index
    description or an alias written from a hostile document still reaches the run, in its task or (after the
    second commit) its system prompt. The run can only read, so the worst case is a wrong answer.
13. **SPEC.md described the old reading order.** I changed one sentence of SPEC.md (in "Asking and evaluating"):
    it now says the program hands the run the map, and gives the reading order the prompts use. The owner should
    read that sentence: SPEC.md is theirs.
14. `FILE_AS_UNIT` takes its folders from `check.PER_UNIT_DIRS` and its unit pattern from `grammar.UNIT`.
15. **Tests** for each of the above, and for two cases the last entry said were tested and were not (a name two
    rows share, a unit list over the limit).

The cache change (commit 2 of this entry):
- **What the saved runs show.** In three evals run one after another, the first turn of every sampled wiki-mode
  answer read 5,649 tokens from the prompt cache (the tools and the system prompt, the ask prompt included) and
  wrote 3,800 to 4,050: the task. Each time the same question had been sent with the same task a few minutes
  before, and it was written again, where a repeated sources-mode task was read from cache (0 or 382 tokens
  written). I do not know why a repeat is written again in wiki mode. Either way, text in the task was paid
  for as a cache write on every answer, and text in the system prompt was read.
- **The change.** The part of the map that is the same for every question ("Where to start" and the unit list)
  now ends the system prompt file; the names in the question stay in the task, after the question and any
  retry note. Over the 30 benchmark questions the wiki-mode task goes from a mean of 5,850 characters to 699,
  and the system prompt from 3,482 to 9,656, the same text for every question of one wiki.
- **What it should save, not measured:** about 3,200 tokens an answer read at $0.01 per million instead of
  written at $0.125, so $0.0003 to $0.0004 an answer, and only in a burst of runs (an eval, or asks within the
  cache's few minutes). A lone `wiki ask` writes the cache either way and costs what it did. This is not the
  cost gap of the last entry closed: a paid run has to show it.
- **What it costs.** Wiki text now sits in the system prompt, a more trusted place than the task (finding 12).
  It is the index's own text, written by the Index run from page headings and red flags, fenced and cut as
  above. The alias names, which are closer to the documents' wording, stay in the task. Reverting this commit
  alone puts the whole map back in the task.
- **What may change.** A run now reads the map before the question, not after it.

Judgment calls the owner can reverse:
- **The maybe.** Marking a word instead of dropping it keeps "parent" and "board" at the price of sometimes
  offering "block". Dropping every lowercase one-word match would lose 8 name lines of the 60 benchmark tasks.
- **The stand-in date.** It is the date most facts carry, not the latest: one proxy part's latest fact date is
  a deadline after the filing. A document whose facts mostly describe an earlier period sorts by that period.
- **Fewer other names.** A run now sees 336 other names over the 60 tasks instead of 528. Those left out are
  found by a search for one that is listed, if the run searches for that one.

Changes a paid run has not yet exercised:
- Six passages of each ask prompt, the map's headings, the maybe mark, the shorter name lists and the map's new
  place. Any of them can move a score or a cost in either direction; one measurement of the three sets costs
  about $3.10, and three runs can only show a change in score larger than the 5 to 7 points of noise.
- The cache change shows in a run's first turn, not in the median cost (unchanged code moved the second set's
  wiki-mode median from $0.0032 to $0.0037). In wiki mode `cache_creation_input_tokens` should fall from about
  3,900 to a few hundred, and `cache_read_input_tokens` rise by as much. If they do not, revert the second
  commit.
- The stand-in date and the list over 80 units: neither example wiki has an undated document or 80 units.

Tests: 205 (203 passed, 2 live tests skipped), up from 202. Each of 25 mutants, one per fix or guard of this
entry, was caught by the ask tests, run on a copy of the project.

Cost: $0.00. Total spent so far: $28.91 of $50 by the ledgers; at most $29.02 with the incident of 2026-11-09.

## After M5: one measurement of the review fixes (2026-11-10)

The owner approved one paid measurement of the last entry's two commits (849ff7f, 294a204): three runs of each
question set, all modes, through `wiki eval`. The code did not change during the runs (the hash of `src/` and
`prompts/` was the same before and after). It spent **$2.97** (548 runs, all `success`) against an estimate of
$3.09.

Result (criteria met; median cost per answer). "Before" is the two measurements of the benchmark entry:

| set | mode | before, 1st | before, 2nd | now |
| --- | --- | --- | --- | --- |
| core-tier (41 criteria) | wiki | 107/123 = 87.0%, $0.0032 | 114/123 = 92.7%, $0.0030 | 108/123 = 87.8%, $0.0025 |
| | both | 112/123 = 91.1%, $0.0034 | 109/123 = 88.6%, $0.0035 | 109/123 = 88.6%, $0.0030 |
| | sources (prompt unchanged) | 106/123 = 86.2%, $0.0027 | 107/123 = 87.0%, $0.0024 | 105/123 = 85.4%, $0.0028 |
| quickstart-tier (30 criteria) | wiki | 84/90 = 93.3%, $0.0018 | 83/90 = 92.2%, $0.0019 | 82/90 = 91.1%, $0.0016 |
| | both | 84/90 = 93.3%, $0.0020 | 82/90 = 91.1%, $0.0021 | 83/90 = 92.2%, $0.0020 |
| | sources (prompt unchanged) | 82/90 = 91.1%, $0.0015 | 82/90 = 91.1%, $0.0015 | 84/90 = 93.3%, $0.0016 |
| second set (49 criteria) | wiki | 140/147 = 95.2%, $0.0032 | 138/147 = 93.9%, $0.0037 | 141/147 = 95.9%, $0.0030 |
| | both | 139/147 = 94.6%, $0.0038 | 140/147 = 95.2%, $0.0039 | 142/147 = 96.6%, $0.0035 |
| | sources (prompt unchanged) | 137/147 = 93.2%, $0.0029 | 132/147 = 89.8%, $0.0028 | 134/147 = 91.2%, $0.0028 |

How to read it:
- **The scores did not move.** Over the three sets wiki mode met 331/360 (91.9%), against 331 and 335 before;
  both mode 334/360 (92.8%), against 335 and 331; sources mode 323/360, against 325 and 321. Three runs can only
  show a change larger than the 5 to 7 points of noise, and none of the fixes shows one.
- **The cache change does what it was meant to.** In wiki mode the first turn's cache write fell from a median of
  3,816, 3,834 and 2,248 tokens (core, second set, quickstart) to 1,006, 1,014 and 995, and its cache read rose
  from 5,649 to 8,768 (7,201 on the quickstart wiki). Both mode moved the same way. I had written "a few
  hundred": the names in the question stay in the task, and with their two heading lines they are about 600
  tokens more than a sources-mode task's 382.
- **In an eval, or any burst of asks, wiki mode now costs about what sources mode costs.** Its median answer
  cost fell on every set: from $0.0030 to $0.0032 before to $0.0025 on core-tier, from $0.0018 to $0.0019 to
  $0.0016 on quickstart-tier, and from $0.0032 to $0.0037 to $0.0030 on the second set. In the same runs it is
  11% below sources mode on core-tier, equal on quickstart-tier and 7% above on the second set, where it was
  10% to 32% above. The first-turn tokens are the firm evidence: the change they show is worth $0.0001 to
  $0.0003 an answer, and the rest of the fall is fewer retries, shorter answers and noise (the unchanged sources
  prompt moved from $0.0024 to $0.0028 on core-tier).
- **A lone `wiki ask` costs what it did, or slightly more.** The saving needs another run to have written the
  system prompt a few minutes before. The coldest first turn of a wiki-mode answer wrote 6,697 tokens now and
  6,404 before, so one question asked on its own in wiki mode is as far above a sources-mode one as it was.
- **Fewer answers were asked again:** 8 of 270 (1 in wiki mode, 7 in both mode), against 24 and 12. Eleven tags
  that named a unit's file were repaired. No answer cited an id outside the manifest, no answer was ungraded,
  and every trap was refused in every mode.

Questions that moved by 15 points or more against the six earlier runs (at three runs this is mostly noise; I
read the answers where a fix could be the cause):
- **demo-trap, wiki mode: 12/12 before, 5/6 now.** One answer of three quoted the $1,374M projection after "Not
  in the wiki". None of six did before, and this work reworded that rule, so the rewording may be the cause. One
  case cannot tell. The other two answers said a projection exists and did not quote it.
- **held-financing, both mode: 27/30 before, 11/15 now.** Two answers list the lenders (Blackstone, Blue Owl,
  Ares, CPPIB, PSP, Dogwood) without calling them a group led by the first three, and the grader did not credit
  the criterion. The third looked in the support agreement and missed the debt package.
- **held-ga-accel: wiki 15/30 to 5/15, both 13/30 to 9/15.** The question that has been unstable in every
  measurement. Its name lines were among those this work shortened, so I compared it criterion by criterion: no
  criterion of it fell by half, in either mode.
- quickstart, both mode: qs-regulatory 15/18 to 6/9 (the criterion that ties the clearances to the 8-K of July
  3 was already met only 3 times in 6), qs-termination-fees 24/24 to 10/12, qs-vendor-rows 20/30 to 15/15.
- second set, both mode: ho-exec-payouts 20/24 to 12/12.

What this measurement did not exercise: a document with no date (the stand-in date), a wiki of more than 80
units, and the maybe mark apart from the 8 name lines that carry it. Neither example wiki has the first two.

Reports, in each workspace's `repo/eval/`, all dated 20261111 (UTC):
- core-tier: T014820Z, T015026Z, T015230Z.
- second set (`report-heldout-tier-*`): T015425Z, T015634Z, T015856Z.
- quickstart-tier: T014820Z, T014926Z, T015020Z.

Proposal: if the trap answer matters, restore the old sentence of the projection rule ("say the figure is not
in the wiki and that a projection exists") and add only the words about starting the answer; one eval of
core-tier in wiki mode ($0.12) would not settle a 1-in-3 rate, so I did not try it.

Cost: $2.97. Total spent so far: $31.88 of $50 by the ledgers; at most $31.99 with the incident of 2026-11-09.
The core workspace has $20.92 left under its cap.

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
