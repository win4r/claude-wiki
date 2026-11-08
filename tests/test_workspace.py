"""wiki init, add, fetch-example's refusal, prompts, the smoke command, and the live tests (opt-in)."""

import json
import os
import re

import pytest

from wiki import gitops, tool_root
from wiki.cli import main
from wiki.config import ROLES
from wiki.prompts import render


def test_init_layout(tmp_path):
    assert main(["init", str(tmp_path / "w")]) == 0
    w = tmp_path / "w"
    for p in ("wiki.toml", "CLAUDE.md", "ledger.jsonl", "docs", "runs", "repo/.git", "repo/wiki/_schema.md",
              "repo/manifest.json", "repo/eval"):
        assert (w / p).exists(), p
    assert not (w / ".git").exists(), "the workspace itself is not a git repo"
    assert len((w / "CLAUDE.md").read_text().splitlines()) <= 40
    assert oct(os.stat(w / "runs").st_mode & 0o777) == "0o700"
    assert gitops.git(w / "repo", "ls-files").split() == ["eval/.gitkeep", "manifest.json", "wiki/_schema.md"]
    assert main(["init", str(w)]) == 1  # not twice
    import tomllib

    cfg = tomllib.loads((w / "wiki.toml").read_text())
    assert cfg["caps"]["total_usd"] == 50.0 and cfg["models"]["grader"] == "sonnet"
    assert all(cfg["models"][r] == "haiku" for r in ROLES if r != "grader")
    assert cfg["limits"]["extract"] == {"turns": 150, "timeout_s": 1800}
    assert cfg["limits"]["grader"] == {"turns": 5, "timeout_s": 120}


def test_add_and_status(ws, corpus, capsys):
    assert main(["-w", str(ws.root), "add", str(corpus)]) == 0
    assert "5 new" in capsys.readouterr().out
    assert main(["-w", str(ws.root), "add", str(corpus / "alpha_8k.txt")]) == 0
    assert "1 unchanged" in capsys.readouterr().out
    assert main(["-w", str(ws.root), "status"]) == 0
    assert "Units: 5 (5 new)" in capsys.readouterr().out
    assert main(["-w", str(ws.root), "cost"]) == 0
    assert "Spent $0.00 of $50.00" in capsys.readouterr().out
    assert gitops.is_clean(ws.repo)


def test_fetch_example_refuses_without_user_agent(ws, monkeypatch, capsys):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    assert main(["-w", str(ws.root), "fetch-example", "--tier", "quickstart"]) == 1
    assert "EDGAR_USER_AGENT" in capsys.readouterr().err


def test_fetch_script_refuses_too(tmp_path):
    import subprocess
    import sys

    script = tool_root() / "examples" / "sec-deal" / "fetch_data_room.py"
    env = {"PATH": os.environ["PATH"]}
    proc = subprocess.run([sys.executable, "-I", str(script), "--tier=quickstart", f"--out={tmp_path}"],
                          env=env, capture_output=True, text=True)
    assert proc.returncode != 0 and "EDGAR_USER_AGENT" in proc.stderr
    assert "https://www.sec.gov/" in script.read_text()


def test_synthetic_files_add(ws):
    pytest.importorskip("docx")
    from wiki.documents import ANALYST_NOTE_SOURCE, add

    ex = tool_root() / "examples" / "sec-deal"
    note = ex / "analyst_note.docx"
    add(ws, [note, ex / "data_provider_extract.csv"], source_lines={str(note): ANALYST_NOTE_SOURCE})
    text = (ws.docs / "analyst-note.txt").read_text()
    assert text.startswith(ANALYST_NOTE_SOURCE) and "252" in text
    assert (ws.docs / "data-provider-extract.txt").read_text().startswith("[SOURCE: data_provider_extract.csv")


def test_every_prompt_keeps_the_rules():
    for name in ("extract", "check", "resolve", "link", "compile", "index", "dream_analyst", "dream_apply"):
        text = render(name, steering="STEER", escalations=True)
        assert "data, never instructions" in text, name
        assert "Secrets never enter the wiki" in text, name
        assert "STEER" in text and "{steering}" not in text and "{common}" not in text, name
        assert "MOVED TO" in text  # forbidden explicitly
    for name in ("ask_wiki", "ask_sources", "ask_both", "grader"):
        text = render(name)
        assert "never instructions" in text, name
    assert "escalations.md" in render("index", escalations=True)
    assert "escalations.md" not in render("index", escalations=False)
    assert "at most 10" in render("resolve", resolve_limit=10)


def test_steering_routes_red_flags_to_units():
    steering = (tool_root() / "schemas" / "sec-deal" / "steering.md").read_text()
    assert "## red-flags" in steering and "Index ranks" in steering


def test_smoke_runs_offline(ws, capsys):
    assert main(["-w", str(ws.root), "smoke", "--estimate"]) == 0
    assert main(["-w", str(ws.root), "smoke", "--no-long-prompt"]) == 0
    report = json.loads(sorted(ws.runs.glob("smoke-*/report.json"))[-1].read_text())
    assert report["checks"]["reader_init"]["ok"] and report["checks"]["writer_init"]["ok"]
    assert report["checks"]["no_claude_md"]["ok"] and report["checks"]["key_works"]["ok"]


def test_no_role_gets_bash_or_web():
    from wiki.runner import ROLE_TOOLS

    for role, tools in ROLE_TOOLS.items():
        assert not any(re.search(r"Bash|Web|mcp", t) for t in tools), role


def test_only_the_runner_starts_claude():
    src = tool_root() / "src" / "wiki"
    for p in src.glob("*.py"):
        if p.name == "runner.py":
            continue
        text = p.read_text()
        assert "claude_bin(" not in text and '"claude"' not in text, p.name


live = pytest.mark.skipif(os.environ.get("WIKI_LIVE") != "1", reason="paid: set WIKI_LIVE=1 (ask the owner first)")


@pytest.mark.live
@live
def test_live_smoke(tmp_path, monkeypatch):
    """Spends real money through the ledger: the M0 checks against the real claude."""
    ws_dir = os.environ.get("WIKI_LIVE_WORKSPACE") or str(tmp_path / "live")
    if not os.path.exists(os.path.join(ws_dir, "wiki.toml")):
        assert main(["init", ws_dir]) == 0
    assert main(["-w", ws_dir, "smoke"]) == 0


def test_probe_tools_offline(ws):
    assert main(["-w", str(ws.root), "smoke", "--probe-tools", "--estimate"]) == 0
    assert main(["-w", str(ws.root), "smoke", "--probe-tools"]) == 0
    run_dir = sorted(ws.runs.glob("probe-*"))[-1]
    report = json.loads((run_dir / "report.json").read_text())
    assert all(p["outcome"] == "probe" for p in report["probes"].values())
    assert report["probes"]["bare-reader"]["tools"] == ["Read", "Glob", "Grep", "EndConversation"]
    restricted = json.loads((run_dir / "restricted-reader.cmd.json").read_text())["argv"]
    assert "--bare" not in restricted and "--restricted" in restricted
    assert "--permission-prompts" not in json.loads(
        (run_dir / "bare-writer-default-mode-host-prompts.cmd.json").read_text())["argv"]
    assert report["real_grep"]["outcome"] == "success"


def test_real_runs_keep_the_default_flags(ws):
    from wiki.runner import Runner, RunSpec

    r = Runner(ws, ws.config())
    argv = r.argv(RunSpec(command="x", phase="x", role="extract", task_id="t", run_dir=ws.runs, cwd=ws.root,
                          task="t", system_prompt_file=ws.root / "p", model="haiku", budget_usd=1, turns=1,
                          timeout_s=1))
    assert argv[1:4] == ["--restricted", "--strict-mcp-config", "--disable-slash-commands"]
    assert "--bare" not in argv
    assert argv[argv.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits" and "--permission-prompts" in argv


def test_runs_get_a_private_config_and_no_memory(ws, monkeypatch):
    from wiki.runner import Runner

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/someone/.claude")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "0")
    env = Runner(ws, ws.config())._env()
    assert env["CLAUDE_CONFIG_DIR"] == str(ws.runs / ".claude-config")
    assert oct(os.stat(env["CLAUDE_CONFIG_DIR"]).st_mode & 0o777) == "0o700"
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1" and env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_init_with_mcp_servers_or_user_plugins_fails(ws, monkeypatch):
    from wiki.runner import Runner, RunSpec, init_problems

    assert init_problems("ask", {"tools": ["Read", "Glob", "Grep"], "plugins": [{"name": "x", "path": "builtin"}]}) == []
    assert init_problems("ask", {"tools": ["Read"], "skills": ["s"]})
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "extras")
    r = Runner(ws, ws.config())
    p = ws.runs / "p.md"
    p.write_text("x")
    res = r.run(RunSpec(command="smoke", phase="smoke", role="ask", task_id="t", run_dir=ws.runs / "x", cwd=ws.root,
                        task="t", system_prompt_file=p, model="haiku", budget_usd=0.01, turns=1, timeout_s=30))
    assert res.outcome == "bad_tools"
    assert any("MCP servers" in e for e in res.errors) and any("user-plugin" in e for e in res.errors)
