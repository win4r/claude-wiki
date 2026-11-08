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
