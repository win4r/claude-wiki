# wiki

`wiki` has Claude Code build a Markdown knowledge wiki in a git repo from a folder of documents, answer
questions from it with a source tag on every fact, and keep it correct with reviewed dreaming passes. Every
agent step is one headless, bare-mode `claude -p` run in its own git worktree; the program checks what each
run changed and records what it cost in a ledger. SPEC.md is the full design; DEVLOG.md says where things stand.

## Setup

Requirements: Python 3.11 or later, git, and Claude Code 2.1.302 or later (`claude --version`).

```bash
pip install -e '.[convert]'
```

The `convert` extra adds `python-docx`, `openpyxl`, `python-pptx` and `pypdf` for Word, Excel, PowerPoint and
text-layer PDF files. Plain text, Markdown, HTML, CSV and email need nothing extra.

### The API key and the Console spend limit

Paid runs use bare mode, which never reads your Claude login: they need an API key.

1. In the Claude Console, create a workspace for this tool and **set its spend limit to $50**. The tool's own
   caps rely on Claude Code's client-side cost estimates, which can differ from the bill (for example, Haiku 5.5
   charges more for prompts over 100,000 tokens); the Console limit is the backstop.
2. Create an API key in that workspace and export it before a paid command:
   `export ANTHROPIC_API_KEY=...` (the tool never writes it to a file, log, commit or wiki page).
3. For unattended runs (cron), put an `apiKeyHelper` command in a Claude Code settings file and point
   `runner.api_key_settings` in `wiki.toml` at it; the runner passes it with `--settings`:

```json
{ "apiKeyHelper": "security find-generic-password -s wiki-anthropic-key -w" }
```

## Quick start on the example corpus

```bash
wiki init ~/wikis/sec-deal
cd ~/wikis/sec-deal
export EDGAR_USER_AGENT="Your Name you@example.com"   # SEC asks every client for a contact
wiki fetch-example --tier quickstart
wiki build --estimate
wiki build
wiki ask "What is the per-share merger consideration, and how did it change?"
```

## Commands

Every command finds its workspace from `--workspace <dir>`, `WIKI_WORKSPACE`, or the nearest folder upward
holding `wiki.toml`. Commands that write the workspace or spend money hold `<workspace>/.lock`; a second one
exits with a message (exit code 75).

| Command | What it does | Paid |
| --- | --- | --- |
| `wiki init <dir>` | Creates a workspace: `wiki.toml`, a short `CLAUDE.md`, `docs/`, `runs/` (mode 0700), `ledger.jsonl`, and `repo/` (git) with a seeded `wiki/_schema.md`. `--schema-dir` picks another schema and steering | No |
| `wiki fetch-example --tier quickstart\|core` | Downloads the example SEC filings with the quickstart's fetch script, adds the synthetic Word analyst note and vendor CSV, runs `wiki add` on them, sets the workspace's tier (and build cap), and copies the question sets into `repo/eval/`. Refuses to run without `EDGAR_USER_AGENT` | No |
| `wiki add <paths>` | Converts documents to text starting with a `[SOURCE: ...]` line, splits long ones into `-part<n>` units, stores them read-only in `docs/`, and records them in `manifest.json`. Refuses scanned PDFs | No |
| `wiki build` | Runs the eight phases (convert, batch, extract, check, resolve, link, compile, index) over new or changed units, committing after each phase, then `wiki check`. `--resume` finishes an interrupted build, rerunning only unfinished tasks | Yes |
| `wiki check` | Lints the wiki and enforces its invariants; `--against <ref>` also checks that every per-unit fact line of that commit is still there, unchanged or with only `valid_to`/`retracted` added. Exits 1 on any error | No |
| `wiki ask "<question>"` | One fresh read-only session; `--mode wiki` (the wiki only), `sources` (the documents only) or `both` (default: the wiki first, documents to confirm or fill a gap). Checks every cited unit against the manifest and saves the transcript to `runs/ask/` | Yes |
| `wiki dream` | With at least 5 ask transcripts since the last accepted dream: analysts find where readers struggled, one apply run confirms each finding in a document and files it, the program recompiles and reindexes, all on branch `dream/<timestamp>`, then `wiki check --against` its base. `--accept` merges it and tags `dream-accepted-<timestamp>`; `--discard` deletes it | Yes |
| `wiki eval <file>` | Asks every question in every mode (`--modes` to narrow), grades each answer on Sonnet against its criteria, and writes `repo/eval/report-*.md` and `.json`: score and cost per mode, traps refused, and the break-even point | Yes |
| `wiki cost` | The ledger: spent, remaining, spend by command and phase | No |
| `wiki status` | Units by status, pages by kind, the last build and dream, any open review branch | No |
| `wiki smoke` | M0's live checks of Claude Code (bare-mode key, init tools, Grep in an added folder, denied reads, a long-prompt cost) | Yes, about $0.35 |

Paid commands share `--estimate` (print the estimate and exit), `--max-usd <n>` (lower the command's cap),
`--model <alias>`, `--concurrency <n>` (default 4) and `--approve-over-cap` (the owner approves, once, an
estimate above the command's cap).

## Costs

Every paid command prints its estimate first. The caps live in `wiki.toml`:

| Cap | Default |
| --- | --- |
| All paid runs, in total | $50 |
| One build, quickstart tier | $6 |
| One build, core tier | $25 |
| One question | $0.50 |
| One dream | $3 |
| One eval run | $3 |

- Build estimates start from the knowledge-wiki quickstart's figures, scaled by unit sizes; a question starts at
  $0.02, grading at $0.02 an answer, a dream at $1 plus $0.15 a transcript. After paid runs, the ledger's
  averages per phase and model replace them.
- A command's cap is split across its runs in proportion to their estimates, with 10% held back, and each run
  gets its share as `--max-budget-usd`.
- An estimate above the command's cap stops with exit code 3 until the owner approves it once with
  `--approve-over-cap`. If spent plus the estimate would pass the total, the command refuses (exit code 4) and
  names the shortfall; only the owner raises `caps.total_usd`, by editing `wiki.toml`.
- A run that ends without a result event is charged its full share.
- `ledger.jsonl` is the only record of spend; it is outside git. Default models: Haiku for every agent role,
  Sonnet for the grader, set per role under `[models]`.

## Dreaming nightly

The tool never merges a dream on its own. To run one every night and leave the branch for review, add a cron
line like this (with `runner.api_key_settings` set, so the key comes from your `apiKeyHelper`):

```cron
15 3 * * * cd /Users/you/wikis/sec-deal && /usr/local/bin/wiki dream >> runs/dream-cron.log 2>&1
```

Then review with `git -C repo diff <base>..dream/<timestamp>` and run `wiki dream --accept` or `--discard`.
Whether to install it is your call.

## Layout and safety

- The workspace's `docs/` (normalized text) and `runs/` (transcripts, run inputs, worktrees, build state) are
  outside git; only `repo/` is a git repo. Every run sends document text to the Anthropic API.
- In `repo/wiki/`, every file has one writer. Per-unit files (`notes/`, `addenda/`, `contracts/`, `relations/`,
  `metrics/`, `open_questions/`) are the source layer; the program compiles `entities/<slug>.md` from them, and a
  Compile run may only change the text between a page's current-picture markers.
- A writing run works in its own worktree and may change only the paths it owns; anything else fails the run
  and discards the worktree. Inputs are read-only copies, re-hashed after every run and phase; `_schema.md` is
  verified unchanged after every phase.
- No role gets Bash, web or MCP tools; the runner fails any run whose `system/init` lists more than its role's
  tools plus `EndConversation`.

## Development

```bash
pytest                         # offline: no network, no claude needed (a fake claude stands in)
WIKI_LIVE=1 pytest -m live     # paid: spends real money through the ledger
```

Code adapted from anthropics/claude-quickstarts (MIT) keeps its notice in `NOTICE`.
