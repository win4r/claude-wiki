"""wiki build: eight phases. The program decides what runs and checks every result; Claude runs read and write.

Every writing run gets its own git worktree, made from the last commit, and may change only the paths it owns.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from . import WikiError, gitops
from . import check as check_mod
from . import cost as cost_mod
from .compile import compile_pages, picture_lines
from .grammar import (
    RED_FLAGS, outside_markers, parse_aliases, parse_sections, split_frontmatter,
)
from .ledger import Ledger
from .manifest import Manifest, make_batches
from .prompts import render, write_prompt
from .runner import Runner, RunSpec
from .workspace import make_read_only, private_dir, remove_tree, sha256_file

PHASES = ["convert", "batch", "extract", "check", "resolve", "link", "compile", "index"]
UNIT_DIRS_EXTRACT = ("notes", "contracts", "relations", "metrics", "open_questions")
UNIT_DIRS_CHECK = UNIT_DIRS_EXTRACT + ("addenda",)
PICTURE_MAX_LINES = 30


def now_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def log(msg: str) -> None:
    print(msg, flush=True)


# --- one writing task ------------------------------------------------------------------------------

RETRY_NOTE = ("\n\nYour previous attempt was rejected by the program's checks and discarded, so the wiki is "
              "unchanged; start again and avoid these problems: ")
RETRY_NOTE_CHARS = 2000


@dataclass
class WriterTask:
    task_id: str
    phase: str
    role: str
    prompt: str  # prompts/<prompt>.md
    task_text: str
    owned_files: set[str] = field(default_factory=set)  # paths relative to wiki/
    owned_dirs: tuple[str, ...] = ()  # any file directly under these wiki/ folders
    add_dirs: list[Path] = field(default_factory=list)
    input_chars: int | None = None
    marker_pages: set[str] | None = None  # compile: may change only between markers on these pages
    lifecycle_dirs: tuple[str, ...] = ()  # dream apply: may only add valid_to/retracted to lines here
    prepare: Callable[[Path], None] | None = None  # runs on the worktree's wiki/ before the run
    validate: Callable[[Path, list[str]], list[str]] | None = None  # (wt wiki, changed) -> problems


@dataclass
class TaskOutcome:
    task_id: str
    ok: bool
    reason: str = ""
    cost: float = 0.0
    attempts: int = 0
    changed: list[str] = field(default_factory=list)
    text: str = ""
    feedback: bool = False  # the failure came from the program's checks, so the retry is told about it


class Context:
    """What a pipeline needs: the workspace, settings, runner, the repo commits go to, and the run's state."""

    def __init__(self, ws, command: str, run_id: str, base_repo: Path, cfg=None, runner: Runner | None = None,
                 model_override: str | None = None, concurrency: int | None = None, shares: dict | None = None,
                 cap: float | None = None):
        self.ws = ws
        self.cfg = cfg or ws.config()
        self.command = command
        self.run_id = run_id
        self.base_repo = Path(base_repo)
        self.run_dir = private_dir(ws.runs / run_id)
        self.ledger = Ledger(ws.ledger_path)
        self.runner = runner or Runner(ws, self.cfg, self.ledger, concurrency)
        self.model_override = model_override
        self.concurrency = concurrency or int(self.cfg.runner("concurrency", 4))
        self.shares = shares or {}
        self.cap = cap
        self.state_lock = threading.Lock()
        self.state: dict = {}
        self._prompts: dict[str, Path] = {}

    @property
    def wiki(self) -> Path:
        return self.base_repo / "wiki"

    def model(self, role: str) -> str:
        return self.model_override or self.cfg.model(role)

    def manifest(self) -> Manifest:
        return Manifest.load(self.base_repo / "manifest.json")

    def save_manifest(self, m: Manifest) -> None:
        m.save(self.base_repo / "manifest.json")

    def prompt_file(self, prompt: str) -> Path:
        with self.state_lock:
            if prompt not in self._prompts:
                self._prompts[prompt] = self._write_prompt(prompt)
            return self._prompts[prompt]

    def _write_prompt(self, prompt: str) -> Path:
        text = render(prompt, steering=self.cfg.steering_text(), escalations=self.cfg.escalations,
                      resolve_limit=int(self.cfg.runner("resolve_limit", 10)))
        return write_prompt(self.run_dir, prompt, text)

    def budget(self, task_id: str, fallback: float) -> float:
        base = task_id.removesuffix("-retry")
        if base in self.shares:
            return float(self.shares[base])
        # a run the estimate didn't foresee (one more compile group, say): its phase's largest share
        phase = base.rsplit("-", 1)[0]
        same = [v for k, v in self.shares.items() if k.rsplit("-", 1)[0] == phase]
        return float(max(same)) if same else fallback

    # --- state.json
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    def save_state(self) -> None:
        with self.state_lock:
            tmp = self.state_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
            tmp.replace(self.state_path())

    def task_state(self, task_id: str) -> dict:
        return self.state.setdefault("tasks", {}).setdefault(task_id, {"status": "pending", "attempts": 0})

    def set_task(self, task_id: str, **fields) -> None:
        with self.state_lock:
            self.task_state(task_id).update(fields)
        self.save_state()

    def commit(self, message: str) -> bool:
        return gitops.commit_all(self.base_repo, message)

    def rebalance(self) -> None:
        """Split what is left of the cap over the planned tasks not yet done, in proportion to their shares.

        Shares come from estimates made before any run; when early phases cost less than estimated, later runs
        would otherwise be held to budgets that are too small (and a budget stop is not retried).
        """
        if self.cap is None or not self.shares:
            return
        tasks = self.state.get("tasks", {})
        todo = {k: v for k, v in self.shares.items() if tasks.get(k, {}).get("status") != "done"}
        if not todo:
            return
        left = max(0.0, self.cap - self.spent_this_run())
        # half of what is left, so every task can still afford its retry (a run with no result is charged in full);
        # a share is only ever raised, never cut
        new = cost_mod.share(todo, left / 2, float(self.cfg.runner("reserve", 0.10)),
                             floor=float(self.cfg.runner("min_run_usd", 0.10)))
        raised = {k: max(todo[k], new[k]) for k in todo}
        if any(raised[k] > todo[k] * 1.0001 for k in todo):
            self.shares.update(raised)
            self.state["shares"] = self.shares
            self.save_state()

    def spent_this_run(self) -> float:
        return sum(float(e.get("cost_usd") or 0) for e in self.ledger.entries() if e.get("run_id") == self.run_id)


def tree_hash(folder: Path) -> dict[str, str]:
    folder = Path(folder)
    return {str(p.relative_to(folder)): sha256_file(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def owned(task: WriterTask, rel: str) -> bool:
    """rel is relative to the worktree root, e.g. wiki/notes/x.md."""
    if not rel.startswith("wiki/"):
        return False
    path = rel[len("wiki/"):]
    if path in task.owned_files:
        return True
    parts = path.split("/")
    return len(parts) == 2 and parts[0] in task.owned_dirs and parts[1].endswith(".md")


def execute(ctx: Context, task: WriterTask) -> TaskOutcome:
    """Run one writing task in its own worktree, check what it changed, merge it or discard it; retry once."""
    out = TaskOutcome(task.task_id, False)
    prior = ctx.task_state(task.task_id) if ctx.state is not None else {}
    if prior.get("status") in ("failed", "running") and prior.get("reason"):  # a resumed task learns why it failed last time
        out.reason = prior["reason"]
        out.feedback = not out.reason.startswith(("run ended:", "the command's cap", "the total budget"))
    attempts = 2
    for attempt in range(1, attempts + 1):
        tid = task.task_id if attempt == 1 else f"{task.task_id}-retry"
        run_task = task
        if out.reason and out.feedback:
            # tell the retry what the program rejected, so it does not repeat it
            run_task = dataclasses.replace(task, task_text=task.task_text + RETRY_NOTE + out.reason[:RETRY_NOTE_CHARS])
        result, reason, changed, text, retryable = _attempt(ctx, run_task, tid)
        out.cost += result
        out.attempts = attempt
        if reason == "":
            out.ok, out.changed, out.text, out.reason = True, changed, text, ""
            return out
        out.reason = reason
        out.feedback = not reason.startswith(("run ended:", "the command's cap", "the total budget"))
        log(f"  {tid}: failed ({reason})")
        if not retryable:
            break
    return out


def _attempt(ctx: Context, task: WriterTask, tid: str):
    budget = ctx.budget(task.task_id, 0.5)
    if ctx.cap is not None and ctx.spent_this_run() + budget > ctx.cap * 1.0001:
        return 0.0, f"the command's cap of {cost_mod.usd(ctx.cap)} would be passed", [], "", False
    if ctx.ledger.spent() + budget > ctx.cfg.total_usd:
        return 0.0, "the total budget would be passed", [], "", False
    wt = ctx.run_dir / "wt" / tid
    if wt.exists():
        gitops.worktree_remove(ctx.base_repo, wt)
    gitops.worktree_add(ctx.base_repo, wt, "HEAD")
    head = gitops.head(wt)
    try:
        if task.prepare:
            task.prepare(wt / "wiki")
        spec = RunSpec(
            command=ctx.command, phase=task.phase, role=task.role, task_id=tid, run_dir=ctx.run_dir,
            cwd=wt / "wiki", task=task.task_text, system_prompt_file=ctx.prompt_file(task.prompt),
            model=ctx.model(task.role), budget_usd=budget, turns=ctx.cfg.turns(task.role),
            timeout_s=ctx.cfg.timeout(task.role), add_dirs=list(task.add_dirs), input_chars=task.input_chars,
        )
        before = {str(d): tree_hash(d) for d in task.add_dirs}
        rr = ctx.runner.run(spec)
        touched = [d for d in task.add_dirs if tree_hash(d) != before[str(d)]]
        if touched:
            raise WikiError(f"{tid} changed its read-only input {touched[0]}; stopped.")
        if not rr.ok:
            return rr.cost_usd, f"run ended: {rr.outcome}", [], "", rr.retryable
        changed = gitops.changed_paths(wt)
        problems = ownership_problems(ctx, task, wt, head, changed)
        if not problems and task.validate:
            problems = task.validate(wt / "wiki", changed)
        if problems:
            (ctx.run_dir / f"{tid}.violations.txt").write_text("\n".join(problems) + "\n")
            return rr.cost_usd, "; ".join(problems[:3]) + (" ..." if len(problems) > 3 else ""), [], "", True
        merge_into(ctx.base_repo, wt, changed)
        return rr.cost_usd, "", changed, rr.text, False
    finally:
        gitops.worktree_remove(ctx.base_repo, wt)  # a worktree is never kept


def ownership_problems(ctx: Context, task: WriterTask, wt: Path, head: str, changed: list[str]) -> list[str]:
    problems = []
    for rel in changed:
        path = rel[len("wiki/"):] if rel.startswith("wiki/") else rel
        if task.marker_pages is not None:
            if path not in task.marker_pages:
                problems.append(f"changed {rel}, which this run does not own")
                continue
            before = gitops.show(wt, head, rel) or ""
            after_file = wt / rel
            after = after_file.read_text() if after_file.exists() else ""
            if not after or outside_markers(after) != outside_markers(before) or "current-picture:start" not in after:
                problems.append(f"changed {rel} outside its current-picture markers")
            elif picture_lines(after) > PICTURE_MAX_LINES:
                problems.append(f"{rel}: current picture is over {PICTURE_MAX_LINES} lines")
            continue
        if task.lifecycle_dirs and path.split("/")[0] in task.lifecycle_dirs and len(path.split("/")) == 2:
            before = gitops.show(wt, head, rel)
            after_file = wt / rel
            after = after_file.read_text() if after_file.exists() else None
            if before is None or after is None:
                problems.append(f"{rel}: may only mark existing lines, not create or delete files")
                continue
            problems += _lifecycle_problems(rel, before, after)
            continue
        if not owned(task, rel):
            problems.append(f"changed {rel}, which this run does not own")
    return problems


def _lifecycle_problems(rel: str, before: str, after: str) -> list[str]:
    from .grammar import lifecycle_only_added

    b, a = before.splitlines(), after.splitlines()
    if len(b) != len(a):
        return [f"{rel}: lines were added or removed; only valid_to or retracted may be added"]
    return [f"{rel}: line {i} changed beyond valid_to/retracted" for i, (x, y) in enumerate(zip(b, a), 1)
            if x != y and not lifecycle_only_added(x, y)]


def merge_into(repo: Path, wt: Path, changed: list[str]) -> None:
    for rel in changed:
        src, dst = wt / rel, repo / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            dst.unlink(missing_ok=True)


def run_parallel(ctx: Context, tasks: list[WriterTask]) -> dict[str, TaskOutcome]:
    results: dict[str, TaskOutcome] = {}
    ctx.rebalance()
    todo = [t for t in tasks if ctx.task_state(t.task_id).get("status") != "done"]
    for t in tasks:
        if t not in todo:
            log(f"  {t.task_id}: done earlier")

    def one(t: WriterTask) -> TaskOutcome:
        ctx.set_task(t.task_id, status="running", phase=t.phase)
        res = execute(ctx, t)
        st = ctx.task_state(t.task_id)
        ctx.set_task(t.task_id, status="done" if res.ok else "failed", reason=res.reason,
                     attempts=st.get("attempts", 0) + res.attempts, cost=round(st.get("cost", 0) + res.cost, 6))
        log(f"  {t.task_id}: {'done' if res.ok else 'FAILED'} ({cost_mod.usd(res.cost)})")
        return res

    if not todo:
        return results
    with ThreadPoolExecutor(max_workers=max(1, ctx.concurrency)) as pool:
        for res in pool.map(one, todo):
            results[res.task_id] = res
    return results


# --- shared checks -----------------------------------------------------------------------------------

def lint_changed(ctx_manifest: Manifest, wiki: Path, changed: list[str], rules=("frontmatter", "tag", "notes", "unit")):
    paths = {c[len("wiki/"):] for c in changed if c.startswith("wiki/")}
    findings = check_mod.run_checks(wiki, ctx_manifest)
    return [str(f) for f in findings if f.severity == "error" and f.rule in rules and f.path in paths]


def input_folder(ctx: Context, name: str, files: dict[str, Path | str]) -> Path:
    """A read-only folder of copies (or texts) for one run, outside git."""
    folder = ctx.run_dir / "inputs" / name
    if folder.exists():
        remove_tree(folder)
    folder.mkdir(parents=True)
    for fname, src in files.items():
        if isinstance(src, Path):
            shutil.copyfile(src, folder / fname)
        else:
            (folder / fname).write_text(src)
    make_read_only(folder)
    return folder


def verify_sources(ctx: Context, manifest: Manifest) -> None:
    """Every document unchanged, and _schema.md unchanged; stop if not."""
    bad = []
    for u in manifest.live_units():
        p = ctx.ws.docs / f"{u}.txt"
        if not p.exists() or sha256_file(p) != manifest.units[u]["sha256"]:
            bad.append(u)
    if bad:
        raise WikiError(f"Source documents changed or missing during the run: {', '.join(bad[:10])}. Stopped.")
    schema = ctx.wiki / "_schema.md"
    want = manifest.data.get("schema_sha256")
    if not schema.exists() or (want and sha256_file(schema) != want):
        raise WikiError("_schema.md changed. Stopped.")


# --- the phases --------------------------------------------------------------------------------------

def unit_text_chars(ws, units) -> int:
    return sum((ws.docs / f"{u}.txt").stat().st_size for u in units)


def extract_tasks(ctx: Context, batches: dict[str, list[str]], phase: str) -> list[WriterTask]:
    tasks = []
    for bid, units in batches.items():
        folder = input_folder(ctx, f"{phase}-{bid}", {f"{u}.txt": ctx.ws.docs / f"{u}.txt" for u in units})
        dirs = UNIT_DIRS_EXTRACT if phase == "extract" else UNIT_DIRS_CHECK
        owned_files = {f"{d}/{u}.md" for d in dirs for u in units}
        names = ", ".join(f"{u}.txt" for u in units)
        if phase == "extract":
            text = (f"Extract: file the units in {folder} ({names}) into this wiki. "
                    f"Read _schema.md first. Your units: {', '.join(units)}.")
        else:
            text = (f"Check: verify and complete what was filed from the units in {folder} ({names}). "
                    f"Read _schema.md first. Your units: {', '.join(units)}.")

        def prepare(wiki: Path, units=units, phase=phase):
            if phase == "extract":  # a re-extracted unit starts from a clean slate; its addenda stay
                for d in UNIT_DIRS_EXTRACT:
                    for u in units:
                        (wiki / d / f"{u}.md").unlink(missing_ok=True)

        def validate(wiki: Path, changed: list[str], units=units):
            m = ctx.manifest()
            problems = [f"no notes/{u}.md" for u in units if not (wiki / "notes" / f"{u}.md").exists()]
            return problems + lint_changed(m, wiki, changed)

        tasks.append(WriterTask(
            task_id=f"{phase}-{bid}", phase=phase, role=phase, prompt=phase, task_text=text,
            owned_files=owned_files, add_dirs=[folder], input_chars=unit_text_chars(ctx.ws, units),
            prepare=prepare, validate=validate,
        ))
    return tasks


def open_question_count(wiki: Path) -> int:
    n = 0
    for p in (wiki / "open_questions").glob("*.md"):
        for line in p.read_text().splitlines():
            s = line.strip()
            if s.startswith("- ") and not s[2:].startswith(("RESOLVED:", "UNRESOLVABLE:")):
                n += 1
    return n


def dispositions(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip().startswith(("- RESOLVED:", "- UNRESOLVABLE:")))


def resolve_task(ctx: Context) -> WriterTask:
    limit = int(ctx.cfg.runner("resolve_limit", 10))

    def validate(wiki, changed):
        problems = lint_changed(ctx.manifest(), wiki, changed)
        new = 0
        for rel in changed:
            if rel.startswith("wiki/open_questions/") and (wiki.parent / rel).exists():
                before = gitops.show(wiki.parent, "HEAD", rel) or ""
                new += max(0, dispositions((wiki.parent / rel).read_text()) - dispositions(before))
        if new > limit:
            problems.append(f"marked {new} questions RESOLVED or UNRESOLVABLE; at most {limit} per build")
        return problems

    return WriterTask(
        task_id="resolve", phase="resolve", role="resolve", prompt="resolve",
        task_text=(f"Resolve: work at most {limit} of the open questions in open_questions/. "
                   f"The source units are in {ctx.ws.docs}. Read _schema.md first."),
        owned_dirs=("addenda", "open_questions"), add_dirs=[ctx.ws.docs], validate=validate,
    )


def unmapped_headings(wiki: Path) -> tuple[dict, list]:
    files = check_mod.read_wiki(wiki)
    rows, _ = parse_aliases(files.get("_aliases.md", ""))
    heads = check_mod.heading_map(files)
    unmapped = {k: v for k, v in heads.items()
                if not [r for r in rows if k[1] in r.proposed or f"{k[0]}#{k[1]}" in r.proposed]}
    return unmapped, rows


def link_task(ctx: Context) -> WriterTask | None:
    unmapped, rows = unmapped_headings(ctx.wiki)
    if not unmapped:
        return None
    lines = ["# Unmapped headings", ""]
    for (unit, heading), rec in sorted(unmapped.items()):
        lines.append(f"## {unit}#{heading}")
        lines.append(f"unit: {unit}")
        lines.append(f"heading: {heading}")
        lines.append(f"type: {rec['type'] or ''}")
        lines.append(f"aliases: {json.dumps(rec['aliases'], ensure_ascii=False)}")
        for fact in rec["facts"][:3]:
            lines.append(fact)
        lines.append("")
    lines += ["# Current rows of _aliases.md", ""]
    lines += [f"- {r.slug} | {r.type} | {json.dumps(r.names, ensure_ascii=False)} | {', '.join(r.proposed)}"
              for r in rows] or ["(none yet)"]
    folder = input_folder(ctx, "link", {"headings.md": "\n".join(lines) + "\n"})
    old = {r.slug: set(r.proposed) for r in rows}

    def validate(wiki, changed):
        problems = lint_changed(ctx.manifest(), wiki, changed, rules=("frontmatter",))
        text = (wiki / "_aliases.md").read_text() if (wiki / "_aliases.md").exists() else ""
        new_rows, parse_problems = parse_aliases(text)
        problems += parse_problems
        new = {r.slug: set(r.proposed) for r in new_rows}
        for slug, proposed in old.items():
            if slug not in new:
                problems.append(f"slug {slug!r} was removed or renamed")
            elif not proposed <= new[slug]:
                problems.append(f"headings were taken out of row {slug!r}")
        files = check_mod.read_wiki(wiki)
        for (unit, heading) in check_mod.heading_map(files):
            hits = [r.slug for r in new_rows if heading in r.proposed or f"{unit}#{heading}" in r.proposed]
            if len(hits) != 1:
                problems.append(f"heading {unit}#{heading} is mapped {len(hits)} times")
        return problems

    return WriterTask(
        task_id="link", phase="link", role="link", prompt="link",
        task_text=f"Link: map every heading listed in {folder}/headings.md in _aliases.md. Read _schema.md first.",
        owned_files={"_aliases.md"}, add_dirs=[folder], validate=validate,
    )


def compile_tasks(ctx: Context, slugs: list[str]) -> list[WriterTask]:
    size = int(ctx.cfg.runner("compile_group", 10))
    groups = [slugs[i:i + size] for i in range(0, len(slugs), size)]
    tasks = []
    for n, group in enumerate(groups, 1):
        pages = {f"entities/{s}.md" for s in group}

        def validate(wiki, changed):
            return lint_changed(ctx.manifest(), wiki, changed, rules=("tag",))

        tasks.append(WriterTask(
            task_id=f"compile-{n:02d}", phase="compile", role="compile", prompt="compile",
            task_text="Compile: write the Current picture of these pages: " + ", ".join(sorted(pages)) + ".",
            marker_pages=pages, validate=validate,
        ))
    return tasks


def catalog(ctx: Context) -> str:
    files = check_mod.read_wiki(ctx.wiki)
    m = ctx.manifest()
    out = ["# Pages", ""]
    for path, text in sorted(files.items()):
        if path in ("_index.md", "README.md", "escalations.md"):
            continue
        fm, body, _ = split_frontmatter(text)
        heads = [ln for ln in body.splitlines() if ln.startswith("#")][:12]
        out.append(f"## {path}")
        for k in ("name", "description", "aliases", "type"):
            if k in fm:
                out.append(f"{k}: {fm[k]}")
        if heads:
            out.append("headings: " + " / ".join(h.lstrip("# ").strip() for h in heads))
        out.append("")
    out += ["# Units", ""]
    for u in m.live_units():
        src = ""
        p = ctx.ws.docs / f"{u}.txt"
        if p.exists():
            with open(p, encoding="utf-8", errors="replace") as handle:
                src = handle.readline().strip()
        per_unit = [f"{d}/{u}.md" for d in check_mod.PER_UNIT_DIRS if f"{d}/{u}.md" in files]
        out.append(f"- {u}: {src} | files: {', '.join(per_unit)}")
    out += ["", "# Red flags", ""]
    for path, text in sorted(files.items()):
        if check_mod.kind_of(path) not in ("notes", "addenda"):
            continue
        for s in parse_sections(text):
            if s.heading == RED_FLAGS and s.facts:
                out.append(f"## from {path}")
                out += s.facts
                out.append("")
    return "\n".join(out) + "\n"


def index_task(ctx: Context) -> WriterTask:
    folder = input_folder(ctx, "index", {"catalog.md": catalog(ctx)})
    owned_files = {"_index.md", "README.md"} | ({"escalations.md"} if ctx.cfg.escalations else set())

    def validate(wiki, changed):
        m = ctx.manifest()
        findings = check_mod.run_checks(wiki, m)
        return [str(f) for f in findings if f.severity == "error" and f.rule in ("index", "frontmatter", "tag")
                and f.path in owned_files]

    return WriterTask(
        task_id="index", phase="index", role="index", prompt="index",
        task_text=f"Index: write {', '.join(sorted(owned_files))} from {folder}/catalog.md. Read _schema.md first.",
        owned_files=owned_files, add_dirs=[folder], validate=validate,
    )


def phase_compile(ctx: Context, phase_no: int = 7) -> list[str]:
    """Program pages, commit, then Compile runs for pages whose facts changed. Returns failed task ids."""
    m = ctx.manifest()
    known = {s: rec.get("facts_sha") for s, rec in m.entities.items()}
    res = compile_pages(ctx.wiki, known)
    for s in res.removed:
        m.entities.pop(s, None)
    ctx.save_manifest(m)
    ctx.commit(f"{ctx.command} {ctx.run_id}: phase {phase_no} compile (program pages)")
    log(f"  pages written {len(res.written)}, removed {len(res.removed)}, need a current picture {len(res.needs_picture)}")
    tasks = compile_tasks(ctx, res.needs_picture)
    results = run_parallel(ctx, tasks)
    failed = [t.task_id for t in tasks if ctx.task_state(t.task_id).get("status") != "done"]
    m = ctx.manifest()
    for t in tasks:
        if ctx.task_state(t.task_id).get("status") == "done":
            for page in t.marker_pages or ():
                slug = Path(page).stem
                m.entities[slug] = {"facts_sha": res.facts_sha.get(slug)}
    for slug, sha in res.facts_sha.items():
        m.entities.setdefault(slug, {"facts_sha": None})
    ctx.save_manifest(m)
    del results
    return failed


# --- the build ---------------------------------------------------------------------------------------

def latest_unfinished(ws) -> Path | None:
    candidates = sorted(ws.runs.glob("build-*/state.json"))
    for p in reversed(candidates):
        try:
            st = json.loads(p.read_text())
        except ValueError:
            continue
        if not st.get("finished"):
            return p
    return None


def plan(ws, cfg, manifest: Manifest, units: list[str], model_override: str | None):
    batch_chars = int(cfg.runner("batch_chars", 120_000))
    sizes = {u: int(manifest.units[u]["size"]) for u in units}
    batches = make_batches(units, sizes, batch_chars)
    models = {r: model_override or cfg.model(r) for r in
              ("extract", "check", "resolve", "link", "compile", "index")}
    averages = Ledger(ws.ledger_path).averages()
    est_entities = max(1, len(units) * 4)
    groups = max(1, -(-est_entities // int(cfg.runner("compile_group", 10))))
    est = cost_mod.estimate_build({b: sum(sizes[u] for u in us) for b, us in batches.items()},
                                  sum(sizes.values()), models, averages, groups)
    return batches, est


def build(ws, resume: bool = False, estimate_only: bool = False, max_usd: float | None = None,
          model: str | None = None, concurrency: int | None = None, approve_over_cap: bool = False) -> int:
    cfg = ws.config()
    ledger = Ledger(ws.ledger_path)
    unfinished = latest_unfinished(ws)
    if resume:
        if not unfinished:
            raise WikiError("No interrupted build to resume.")
        state = json.loads(unfinished.read_text())
        run_id = state["build_id"]
    else:
        if unfinished and not estimate_only:
            raise WikiError(f"Build {unfinished.parent.name} did not finish: run wiki build --resume to finish it.")
        state = None
        run_id = f"build-{now_id()}"
    manifest = Manifest.load(ws.manifest_path)
    if state:
        units = state["units"]
        batches = state["batches"]
        shares, cap = state["shares"], state["cap"]
        model = model or state.get("model")
        log(f"Resuming {run_id}: {len(units)} unit(s)")
    else:
        units = manifest.pending_units()
        if not units:
            log("Nothing to build: every unit is filed. (wiki add new or changed documents first.)")
            return 0
        batches, est = plan(ws, cfg, manifest, units, model)
        log(f"Estimate for wiki build: {cost_mod.usd(est.total)}")
        for line in est.lines:
            log("  " + line)
        cap_limit = min(cfg.build_cap(), max_usd) if max_usd else cfg.build_cap()
        log(f"  Cap {cost_mod.usd(cap_limit)}; spent so far {cost_mod.usd(ledger.spent())} of {cost_mod.usd(cfg.total_usd)}.")
        if estimate_only:
            return 0
        Runner(ws, cfg, ledger).check_ready()
        cap = cost_mod.authorize(ledger.spent(), cfg.total_usd, est.total, cap_limit, approve_over_cap,
                                 "this build", float(cfg.runner("reserve", 0.10)))
        shares = cost_mod.share(est.tasks, cap, float(cfg.runner("reserve", 0.10)),
                                floor=float(cfg.runner("min_run_usd", 0.10)))
        state = {"build_id": run_id, "units": units, "batches": batches, "shares": shares, "cap": cap,
                 "estimate": est.total, "model": model, "phases_done": [], "tasks": {}, "finished": False,
                 "started": datetime.now(UTC).isoformat(timespec="seconds")}
    if estimate_only:
        log(f"Resume of {run_id} would run the unfinished tasks within the remaining cap.")
        return 0
    ctx = Context(ws, "build", run_id, ws.repo, cfg=cfg, model_override=model, concurrency=concurrency,
                  shares=shares, cap=cap)
    ctx.runner.check_ready()
    ctx.state = state
    ctx.save_state()
    start = time.monotonic()
    try:
        _run_phases(ctx)
    except WikiError as why:
        ctx.state["stopped"] = str(why)
        ctx.state["spent"] = round(ctx.spent_this_run(), 6)
        ctx.save_state()
        log(f"Build {run_id} stopped: {why}")
        log(f"Spent on this build: {cost_mod.usd(ctx.spent_this_run())}. Fix the cause, then run wiki build --resume.")
        return 2
    ctx.state["finished"] = True
    ctx.state["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    ctx.state["spent"] = round(ctx.spent_this_run(), 6)
    ctx.save_state()
    log(f"Build {run_id} finished in {time.monotonic() - start:.0f}s: spent {cost_mod.usd(ctx.state['spent'])} "
        f"(estimate {cost_mod.usd(ctx.state.get('estimate', 0))}, cap {cost_mod.usd(ctx.cap or 0)}).")
    return 0


def _done(ctx: Context, phase: str) -> bool:
    return phase in ctx.state.get("phases_done", [])


def _finish_phase(ctx: Context, n: int, phase: str) -> None:
    verify_sources(ctx, ctx.manifest())
    ctx.commit(f"build {ctx.run_id}: phase {n} {phase}")
    ctx.state.setdefault("phases_done", []).append(phase)
    ctx.save_state()


def _stop_if_failed(ctx: Context, tasks, phase: str, n: int) -> None:
    failed = [t.task_id for t in tasks if ctx.task_state(t.task_id).get("status") != "done"]
    if failed:
        ctx.commit(f"build {ctx.run_id}: phase {n} {phase} (partial)")
        reasons = "; ".join(f"{t}: {ctx.task_state(t).get('reason', '')}" for t in failed)
        raise WikiError(f"phase {n} {phase}: {len(failed)} task(s) failed ({reasons})")


def _run_phases(ctx: Context) -> None:
    units, batches = ctx.state["units"], ctx.state["batches"]
    log(f"Build {ctx.run_id}: {len(units)} unit(s) in {len(batches)} batch(es)")
    if not _done(ctx, "convert"):
        log("Phase 1 convert")
        _finish_phase(ctx, 1, "convert")
    if not _done(ctx, "batch"):
        log("Phase 2 batch")
        m = ctx.manifest()
        for bid, us in batches.items():
            for u in us:
                m.units[u]["batch"] = f"{ctx.run_id}/{bid}"
        ctx.save_manifest(m)
        _finish_phase(ctx, 2, "batch")
    if not _done(ctx, "extract"):
        log("Phase 3 extract")
        tasks = extract_tasks(ctx, batches, "extract")
        run_parallel(ctx, tasks)
        m = ctx.manifest()
        for t in tasks:
            bid = t.task_id.split("-", 1)[1]
            ok = ctx.task_state(t.task_id).get("status") == "done"
            m.set_status(batches[bid], "extracted" if ok else "failed")
        ctx.save_manifest(m)
        missing = [u for u in units if not (ctx.wiki / "notes" / f"{u}.md").exists()
                   and m.units[u]["status"] != "failed"]
        if missing:
            raise WikiError(f"units without a notes file after extraction: {', '.join(missing)}")
        failed_units = [u for u in units if m.units[u]["status"] == "failed"]
        if failed_units:
            ctx.commit(f"build {ctx.run_id}: phase 3 extract (partial)")
            raise WikiError(
                "extraction failed for " + ", ".join(failed_units) + "; the build stops after phase 3, because a "
                "wiki missing documents answers as if they said nothing")
        _finish_phase(ctx, 3, "extract")
    if not _done(ctx, "check"):
        log("Phase 4 check")
        tasks = extract_tasks(ctx, batches, "check")
        run_parallel(ctx, tasks)
        _stop_if_failed(ctx, tasks, "check", 4)
        m = ctx.manifest()
        m.set_status(units, "checked")
        ctx.save_manifest(m)
        _finish_phase(ctx, 4, "check")
    if not _done(ctx, "resolve"):
        log("Phase 5 resolve")
        if open_question_count(ctx.wiki):
            tasks = [resolve_task(ctx)]
            run_parallel(ctx, tasks)
            _stop_if_failed(ctx, tasks, "resolve", 5)
        else:
            log("  no open questions")
        _finish_phase(ctx, 5, "resolve")
    if not _done(ctx, "link"):
        log("Phase 6 link")
        t = link_task(ctx)
        if t:
            run_parallel(ctx, [t])
            _stop_if_failed(ctx, [t], "link", 6)
        else:
            log("  every heading is mapped")
        unmapped, _ = unmapped_headings(ctx.wiki)
        if unmapped:
            raise WikiError(f"{len(unmapped)} heading(s) still unmapped after Link")
        _finish_phase(ctx, 6, "link")
    if not _done(ctx, "compile"):
        log("Phase 7 compile")
        failed = phase_compile(ctx)
        if failed:
            ctx.commit(f"build {ctx.run_id}: phase 7 compile (partial)")
            raise WikiError(f"phase 7 compile: {', '.join(failed)} failed")
        _check_pages(ctx)
        _finish_phase(ctx, 7, "compile")
    if not _done(ctx, "index"):
        log("Phase 8 index")
        t = index_task(ctx)
        ctx.state.setdefault("tasks", {}).pop("index", None)  # index always reruns
        run_parallel(ctx, [t])
        _stop_if_failed(ctx, [t], "index", 8)
        _finish_phase(ctx, 8, "index")
    log("wiki check")
    findings = check_mod.run_checks(ctx.wiki, ctx.manifest(), repo=ctx.base_repo)
    errs = check_mod.errors(findings)
    (ctx.run_dir / "check.txt").write_text(check_mod.report(findings, show=10_000) + "\n")
    if errs:
        log(check_mod.report(findings, show=40))
        raise WikiError(f"wiki check found {len(errs)} error(s); see {ctx.run_dir / 'check.txt'}")
    m = ctx.manifest()
    m.set_status(units, "filed")
    ctx.save_manifest(m)
    ctx.commit(f"build {ctx.run_id}: filed {len(units)} unit(s)")
    log(check_mod.report(findings, show=10).splitlines()[-1])


def _check_pages(ctx: Context) -> None:
    files = check_mod.read_wiki(ctx.wiki)
    rows, _ = parse_aliases(files.get("_aliases.md", ""))
    heads = check_mod.heading_map(files)
    mapped = {r.slug for r in rows for (u, h), rec in heads.items()
              if rec["facts"] and (h in r.proposed or f"{u}#{h}" in r.proposed)}
    missing = [s for s in mapped if f"entities/{s}.md" not in files]
    if missing:
        raise WikiError(f"slugs without a page after compile: {', '.join(sorted(missing)[:10])}")
