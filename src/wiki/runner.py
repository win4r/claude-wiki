"""The only module that starts `claude`. One headless, restricted-mode run per task.

Claude Code 2.1.319's `--bare` mode has a fixed tool set (Bash, Edit, Read) that `--tools` can only narrow, so
runs use `--restricted --strict-mcp-config --disable-slash-commands` instead (owner-approved change from SPEC,
2026-11-08). What bare mode skipped is turned off here: a private, empty CLAUDE_CONFIG_DIR per workspace (no user
settings, plugins, hooks or login to fall back on), CLAUDE.md and auto-memory disabled by environment, and the
system/init event checked for MCP servers, skills and non-built-in plugins.

Every run: checked tools, a saved stream, a ledger line (even on failure), a timeout that sends SIGINT then
SIGTERM, and at most `concurrency` processes at once. An interrupted command (Ctrl-C) stops its runs, records each
one, and starts no other.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from . import WikiError
from .ledger import Ledger

WRITER_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
READER_TOOLS = ("Read", "Glob", "Grep")
ROLE_TOOLS = {
    "extract": WRITER_TOOLS,
    "check": WRITER_TOOLS,
    "resolve": WRITER_TOOLS,
    "link": WRITER_TOOLS,
    "index": WRITER_TOOLS,
    "compile": ("Read", "Edit"),
    "dream_apply": WRITER_TOOLS,
    "dream_analyst": READER_TOOLS,
    "ask": READER_TOOLS,
    "grader": ("Read",),
}
WRITER_ROLES = {"extract", "check", "resolve", "link", "index", "compile", "dream_apply"}
ALWAYS_PRESENT = {"EndConversation"}  # --tools cannot remove it
NOT_RETRYABLE = {"success", "error_max_turns", "error_max_budget_usd", "bad_tools", "probe", "interrupted"}
# Environment passed to claude; nothing else from os.environ reaches the process.
ENV_PASS = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM",
            "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "SHELL")
# Set on every run, whatever the caller's environment says.
ENV_SET = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
RESTRICTED_FLAGS = ["--restricted", "--strict-mcp-config", "--disable-slash-commands"]
SIGINT_GRACE_S = 15.0
SIGTERM_GRACE_S = 10.0


def claude_bin() -> str:
    return os.environ.get("WIKI_CLAUDE_BIN", "claude")


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


@dataclass
class RunSpec:
    command: str  # the wiki command: build, ask, eval, dream, smoke
    phase: str  # ledger phase: extract, check, ..., ask-wiki, grade, dream-analyst
    role: str  # selects tools, permission mode
    task_id: str
    run_dir: Path  # runs/<run-id>
    cwd: Path
    task: str  # short task text that only names input files
    system_prompt_file: Path
    model: str
    budget_usd: float
    turns: int
    timeout_s: float
    add_dirs: list[Path] = field(default_factory=list)
    input_chars: int | None = None
    # diagnostics only (wiki smoke --probe-tools); the defaults are what every real run uses
    tools_override: str | None = None
    bare: bool = False  # probes only: Claude Code's --bare mode instead of RESTRICTED_FLAGS
    permission_mode: str | None = None
    permission_prompts: bool = True
    stop_at_init: bool = False
    check_tools: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    task_id: str
    outcome: str
    cost_usd: float
    session_id: str | None = None
    turns: int | None = None
    text: str = ""
    errors: list = field(default_factory=list)
    permission_denials: list = field(default_factory=list)
    init_tools: list | None = None
    usage: dict = field(default_factory=dict)
    charged_full_cap: bool = False
    stream_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "success"

    @property
    def retryable(self) -> bool:
        return self.outcome not in NOT_RETRYABLE


def usage_of(result: dict) -> dict:
    """Token counts from a result event: modelUsage summed over models when present, else usage."""
    counts = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    model_usage = result.get("modelUsage") or {}
    if model_usage:
        for mu in model_usage.values():
            counts["input_tokens"] += int(mu.get("inputTokens", 0) or 0)
            counts["output_tokens"] += int(mu.get("outputTokens", 0) or 0)
            counts["cache_read_tokens"] += int(mu.get("cacheReadInputTokens", 0) or 0)
            counts["cache_write_tokens"] += int(mu.get("cacheCreationInputTokens", 0) or 0)
        return counts
    u = result.get("usage") or {}
    counts["input_tokens"] = int(u.get("input_tokens", 0) or 0)
    counts["output_tokens"] = int(u.get("output_tokens", 0) or 0)
    counts["cache_read_tokens"] = int(u.get("cache_read_input_tokens", 0) or 0)
    counts["cache_write_tokens"] = int(u.get("cache_creation_input_tokens", 0) or 0)
    return counts


def parse_stream(lines) -> tuple[dict | None, dict | None]:
    """(system/init event, final result event) from stream-json lines."""
    init, result = None, None
    for raw in lines:
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init" and init is None:
            init = ev
        elif ev.get("type") == "result":
            result = ev
    return init, result


def tools_ok(role: str, tools: list | None) -> bool:
    if tools is None:
        return False
    return set(tools) - ALWAYS_PRESENT == set(ROLE_TOOLS[role])


def init_problems(role: str, init: dict | None) -> list[str]:
    """What is wrong with a system/init event for this role: tools, MCP servers, skills, non-built-in plugins."""
    if not init:
        return ["no system/init event"]
    problems = []
    if not tools_ok(role, init.get("tools")):
        problems.append(f"tools {init.get('tools')} are not {list(ROLE_TOOLS[role])} (plus EndConversation)")
    if init.get("mcp_servers"):
        problems.append(f"MCP servers {init.get('mcp_servers')}")
    if init.get("skills"):
        problems.append(f"skills {init.get('skills')}")
    extra = [pl.get("name") if isinstance(pl, dict) else pl for pl in init.get("plugins") or []
             if not isinstance(pl, dict) or pl.get("path") != "builtin"]
    if extra:
        problems.append(f"plugins {extra}")
    return problems


class Runner:
    def __init__(self, workspace, config, ledger: Ledger | None = None, concurrency: int | None = None):
        self.ws = workspace
        self.config = config
        self.ledger = ledger or Ledger(workspace.ledger_path)
        n = concurrency or int(config.runner("concurrency", 4))
        self.slots = threading.Semaphore(max(1, n))
        self._version_checked = False
        self._stopping = threading.Event()  # no run starts once this is set
        self._interrupting = threading.Event()  # and the running ones are being stopped
        self._live: dict[subprocess.Popen, dict] = {}  # each running process with its run's state
        self._live_lock = threading.Lock()

    # --- preconditions
    def check_ready(self) -> None:
        """Before any paid command: a key source and a new enough Claude Code."""
        if not os.environ.get("ANTHROPIC_API_KEY") and not self.config.runner("api_key_settings"):
            raise WikiError(
                "No API key: set ANTHROPIC_API_KEY, or set runner.api_key_settings in wiki.toml to a "
                "--settings file with an apiKeyHelper command."
            )
        self.config.steering_text()  # a steering file that is named but missing stops here, not after runs are paid
        self.check_version()

    def check_version(self) -> str:
        if self._version_checked:
            return ""
        want = parse_version(self.config.runner("min_claude_version", "2.1.319"))
        try:
            proc = subprocess.run([claude_bin(), "--version"], capture_output=True, text=True, timeout=60,
                                  env=self._env())
        except (OSError, subprocess.TimeoutExpired) as why:
            raise WikiError(f"Could not run `{claude_bin()} --version`: {why}") from None
        got = parse_version(proc.stdout)
        if not got or (want and got < want):
            raise WikiError(
                f"Claude Code {'.'.join(map(str, want or ()))} or later is required; found "
                f"{proc.stdout.strip() or 'nothing'}."
            )
        self._version_checked = True
        return proc.stdout.strip()

    def _env(self) -> dict:
        from .workspace import private_dir

        env = {k: os.environ[k] for k in ENV_PASS if k in os.environ}
        env.update({k: v for k, v in os.environ.items() if k.startswith("WIKI_FAKE_")})
        env.update(ENV_SET)
        env["CLAUDE_CONFIG_DIR"] = str(private_dir(Path(self.ws.runs) / ".claude-config"))
        return env

    # --- the command line
    def argv(self, spec: RunSpec) -> list[str]:
        mode = spec.permission_mode or ("acceptEdits" if spec.role in WRITER_ROLES else "dontAsk")
        tools = spec.tools_override if spec.tools_override is not None else ",".join(ROLE_TOOLS[spec.role])
        cmd = [claude_bin()] + (["--bare"] if spec.bare else list(RESTRICTED_FLAGS)) + [
            "-p", spec.task,
            "--model", spec.model,
            "--append-system-prompt-file", str(spec.system_prompt_file),
            "--tools", tools,
            "--permission-mode", mode,
        ] + (["--permission-prompts", "none"] if spec.permission_prompts else [])
        cmd += list(spec.extra_args)
        for d in spec.add_dirs:
            cmd += ["--add-dir", str(d)]
        settings = self.config.runner("api_key_settings")
        if settings:  # relative to wiki.toml's folder, since a run's working directory is a worktree
            cmd += ["--settings", str(Path(self.config.path).parent / Path(str(settings)).expanduser())]
        cmd += [
            "--max-budget-usd", f"{max(spec.budget_usd, 0.01):.4f}",
            "--max-turns", str(spec.turns),
            "--output-format", "stream-json", "--verbose",
            "--no-session-persistence",
        ]
        return cmd

    # --- stopping
    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def stop_all(self, running: bool = True) -> None:
        """Start no further run: the command is ending early, so nothing more is paid for. With `running`, the
        runs in progress are stopped too (SIGINT, then SIGTERM) and recorded as interrupted; without it they
        finish, and what they did is kept."""
        self._stopping.set()
        if not running:
            return
        self._interrupting.set()
        with self._live_lock:
            live = list(self._live.items())
        for proc, state in live:
            state["interrupted"] = True
            threading.Thread(target=_stop_process, args=(proc,), daemon=True).start()

    # --- one run
    def run(self, spec: RunSpec) -> RunResult:
        self.check_version()
        run_dir = Path(spec.run_dir)
        from .workspace import private_dir

        private_dir(run_dir)
        stream_path = run_dir / f"{spec.task_id}.jsonl"
        with self.slots:
            if self.stopping:  # the command is ending: this run never starts, and nothing is charged for it
                return RunResult(task_id=spec.task_id, outcome="interrupted", cost_usd=0.0)
            keep_previous(run_dir, spec.task_id)
            argv = self.argv(spec)
            (run_dir / f"{spec.task_id}.cmd.json").write_text(
                json.dumps({"argv": argv, "cwd": str(spec.cwd)}, indent=1)
            )
            init, result, state, bad_tools, exit_code, error = self._execute(spec, argv, stream_path)
        rr = self._finish(spec, init, result, state, bad_tools, exit_code, stream_path)
        if error is not None:  # Ctrl-C reached this thread mid-run: the run is stopped and in the ledger; pass it on
            raise error
        return rr

    def _execute(self, spec, argv, stream_path):
        init = result = error = None
        bad_tools = probed = False
        state = {"timed_out": False, "interrupted": False}
        stderr_path = stream_path.with_suffix(".stderr")
        with open(stream_path, "w", encoding="utf-8") as out, open(stderr_path, "w") as err:
            try:
                proc = subprocess.Popen(
                    argv, cwd=str(spec.cwd), stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                    env=self._env(), text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as why:
                err.write(str(why))
                return None, None, state, False, -1, None
            done = threading.Event()

            def stop(reason: str) -> None:
                if proc.poll() is not None:
                    return
                state[reason] = True
                _stop_process(proc)

            def watchdog():
                if not done.wait(spec.timeout_s):
                    stop("timed_out")

            try:
                with self._live_lock:
                    self._live[proc] = state
                threading.Thread(target=watchdog, daemon=True).start()
                if self._interrupting.is_set():  # stop_all ran while this run was starting
                    threading.Thread(target=stop, args=("interrupted",), daemon=True).start()
                for line in proc.stdout:
                    out.write(line)
                    out.flush()
                    ev = _json(line)
                    if not ev:
                        continue
                    if ev.get("type") == "system" and ev.get("subtype") == "init" and init is None:
                        init = ev
                        if spec.stop_at_init:
                            probed = True
                            threading.Thread(target=stop, args=("probe",), daemon=True).start()
                        elif spec.check_tools and init_problems(spec.role, ev):
                            bad_tools = True
                            threading.Thread(target=stop, args=("bad_tools",), daemon=True).start()
                    elif ev.get("type") == "result":
                        result = ev
                proc.wait()
            except BaseException as why:  # Ctrl-C in the thread that reads the stream: stop the run before leaving
                error = why
                state["interrupted"] = True
                try:
                    _stop_process(proc)
                    proc.wait()
                except BaseException:  # a second Ctrl-C while waiting for it to stop
                    proc.kill()
                    proc.wait()
            finally:
                done.set()
                with self._live_lock:
                    self._live.pop(proc, None)
        if probed:
            bad_tools = "probe"
        elif spec.check_tools and init is None and result is not None:
            bad_tools = True  # it ran to a result without a system/init event: nothing showed which tools it had
        return init, result, state, bad_tools, proc.returncode, error

    def _finish(self, spec, init, result, state, bad_tools, exit_code, stream_path) -> RunResult:
        charged_full = False
        if bad_tools == "probe":
            outcome = "probe"
        elif bad_tools:
            outcome = "bad_tools"
        elif result is None:
            outcome = "timeout" if state["timed_out"] else "interrupted" if state["interrupted"] else "no_result"
        elif result.get("subtype") == "success" and not result.get("is_error"):
            outcome = "success"
        else:
            outcome = result.get("subtype") or "error_during_execution"
            if outcome == "success":
                outcome = "error_during_execution"
        if result is not None:
            try:
                cost, usage = float(result.get("total_cost_usd") or 0.0), usage_of(result)
            except (TypeError, ValueError, AttributeError):  # a result whose cost cannot be read: unknown, so its cap
                cost, usage, charged_full = spec.budget_usd, {}, True
        else:
            cost, usage, charged_full = spec.budget_usd, {}, True  # charge what's unknown its full cap
        if bad_tools and result is None:
            charged_full = True
        rr = RunResult(
            task_id=spec.task_id,
            outcome=outcome,
            cost_usd=cost,
            session_id=(result or init or {}).get("session_id"),
            turns=(result or {}).get("num_turns"),
            text=(result or {}).get("result") or "",
            errors=(init_problems(spec.role, init) if outcome == "bad_tools" else [])
            + list((result or {}).get("errors") or []),
            permission_denials=list((result or {}).get("permission_denials") or []),
            init_tools=(init or {}).get("tools"),
            usage=usage,
            charged_full_cap=charged_full,
            stream_path=stream_path,
        )
        self.ledger.append(
            command=spec.command, phase=spec.phase, task_id=spec.task_id, model=spec.model,
            session_id=rr.session_id, cost_usd=cost, outcome=outcome,
            run_cap_usd=round(spec.budget_usd, 4), input_chars=spec.input_chars,
            charged_full_cap=True if charged_full else None, turns=rr.turns,
            exit_code=exit_code, run_id=Path(spec.run_dir).name,
            permission_denials=len(rr.permission_denials) or None,
            init_problems=(rr.errors[:4] if outcome == "bad_tools" else None),
            **usage,
        )
        return rr


RUN_FILES = (".jsonl", ".stderr", ".cmd.json", ".violations.txt")  # the last is written by phases


def keep_previous(run_dir: Path, task_id: str) -> None:
    """A task run again under the same id (a resumed build) keeps the earlier run's files as <task>.prev<n>.*;
    the latest run always has the plain names."""
    run_dir = Path(run_dir)
    if not any((run_dir / f"{task_id}{s}").exists() for s in RUN_FILES):
        return
    n = 1
    while any((run_dir / f"{task_id}.prev{n}{s}").exists() for s in RUN_FILES):
        n += 1
    for s in RUN_FILES:
        p = run_dir / f"{task_id}{s}"
        if p.exists():
            p.rename(run_dir / f"{task_id}.prev{n}{s}")


def _stop_process(proc) -> None:
    """SIGINT, then SIGTERM, then SIGKILL, each after the grace the one before was given."""
    try:
        proc.send_signal(signal.SIGINT)
        if _wait(proc, SIGINT_GRACE_S):
            return
        proc.send_signal(signal.SIGTERM)
        if _wait(proc, SIGTERM_GRACE_S):
            return
        proc.kill()
    except ProcessLookupError:
        pass


def run_all(runner: Runner, fn, items, workers: int) -> list:
    """fn(item) for every item on a pool of threads; the results, in the items' order.

    When the command is interrupted (Ctrl-C), the running runs are stopped and no other starts. When one item
    raises, no run starts after that, the running ones finish, and the error is raised. Either way the workers
    are back before this returns, and nothing is started that the caller will not use."""
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = [pool.submit(fn, item) for item in items]
    try:
        wait(futures, return_when=FIRST_EXCEPTION)
        for f in futures:  # of the items that raised, the first in the items' order
            failure = f.exception() if f.done() else None
            if failure is not None:
                raise failure
        return [f.result() for f in futures]
    except BaseException as why:
        runner.stop_all(running=not isinstance(why, Exception))
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _wait(proc, seconds: float) -> bool:
    try:
        proc.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def _json(line: str):
    try:
        ev = json.loads(line)
        return ev if isinstance(ev, dict) else None
    except ValueError:
        return None
