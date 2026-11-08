"""Ledger and cap arithmetic, estimates, the runner's command line and stream parsing, and the fake claude."""

import json
import os

import pytest

from wiki import WikiError
from wiki import cost as cost_mod
from wiki.ledger import Ledger
from wiki.runner import ROLE_TOOLS, Runner, RunSpec, parse_stream, parse_version, tools_ok, usage_of


# --- ledger and caps

def test_ledger_lines_and_summary(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append(command="build", phase="extract", task_id="t1", model="haiku", session_id="s", cost_usd=0.5,
               outcome="success", input_tokens=10, input_chars=1000)
    led.append(command="build", phase="extract", task_id="t2", model="haiku", cost_usd=1.0, outcome="no_result",
               charged_full_cap=True)
    led.append(command="ask", phase="ask-wiki", task_id="q", model="haiku", cost_usd=0.02, outcome="success")
    assert led.spent() == pytest.approx(1.52)
    lines = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert set(lines[0]) >= {"time", "command", "phase", "task_id", "model", "session_id", "input_tokens",
                             "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "outcome"}
    s = led.summary()
    assert s["by_command"]["build"] == pytest.approx(1.5) and s["by_phase"][("ask", "ask-wiki")] == pytest.approx(0.02)
    av = led.averages()
    assert av[("extract", "haiku")]["per_run"] == pytest.approx(0.5)  # the uncharged no_result run is excluded
    assert av[("extract", "haiku")]["per_char"] == pytest.approx(0.0005)
    assert oct(os.stat(tmp_path / "ledger.jsonl").st_mode & 0o777) == "0o600"


def test_authorize_hard_stop_and_over_cap():
    assert cost_mod.authorize(10, 50, 2, 6, False, "x") == 6
    with pytest.raises(WikiError) as e:
        cost_mod.authorize(49, 50, 2, 6, False, "x")
    assert e.value.code == 4 and "shortfall $1.00" in str(e.value)
    with pytest.raises(WikiError) as e:
        cost_mod.authorize(0, 50, 7, 6, False, "x")
    assert e.value.code == 3
    approved = cost_mod.authorize(0, 50, 7, 6, True, "x")
    assert approved == pytest.approx(7 / 0.9)
    assert cost_mod.authorize(48, 50, 1, 6, False, "x") == 2  # never more than what remains


def test_share_is_proportional_with_reserve():
    s = cost_mod.share({"a": 1.0, "b": 3.0}, 10.0, 0.10)
    assert s["a"] == pytest.approx(2.25) and s["b"] == pytest.approx(6.75)
    assert sum(s.values()) == pytest.approx(9.0)
    assert cost_mod.share({"a": 0, "b": 0}, 10.0)["a"] == pytest.approx(4.5)


def test_estimates_start_and_calibrate():
    models = {r: "haiku" for r in ("extract", "check", "resolve", "link", "compile", "index")}
    est = cost_mod.estimate_build({"b01": 120_000, "b02": 60_000}, 180_000, models, {}, 3)
    assert set(est.phases) == {"extract", "check", "resolve", "link", "compile", "index"}
    assert len([t for t in est.tasks if t.startswith("compile-")]) == 3
    assert est.total == pytest.approx(sum(est.tasks.values()))
    # a 120k-character batch passes 100,000 prompt tokens: long-context prices apply
    long = cost_mod.priced(cost_mod.worker_tokens(120_000, cost_mod.EXTRACTOR), "haiku")
    short = cost_mod.dollars(cost_mod.worker_tokens(120_000, cost_mod.EXTRACTOR), cost_mod.PRICES["claude-haiku-5-5"])
    assert long > short * 4
    cal = cost_mod.estimate_build({"b01": 1000}, 1000, models, {("extract", "haiku"): {"n": 2, "per_run": 9.0,
                                                                                     "per_char": 0.001}}, 1)
    assert cal.tasks["extract-b01"] == pytest.approx(1.0)
    assert cost_mod.estimate_question({}, "wiki", "haiku") == 0.02
    assert cost_mod.estimate_question({("ask-wiki", "haiku"): {"n": 1, "per_run": 0.05, "per_char": None}}, "wiki",
                                      "haiku") == 0.05
    assert cost_mod.estimate_dream(10) == pytest.approx(2.5)


# --- the runner

def spec(ws, tmp_path, role="ask", task="Ask: what?", **kw):
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    sp = tmp_path / "prompt.md"
    sp.write_text("prompt")
    base = dict(command="test", phase="ask-wiki", role=role, task_id="t1", run_dir=ws.runs / "r1", cwd=cwd,
                task=task, system_prompt_file=sp, model="haiku", budget_usd=0.25, turns=5, timeout_s=30)
    base.update(kw)
    return RunSpec(**base)


def test_command_line(ws, tmp_path):
    r = Runner(ws, ws.config())
    writer = r.argv(spec(ws, tmp_path, role="extract", add_dirs=[tmp_path / "in"]))
    assert writer[1:5] == ["--restricted", "--strict-mcp-config", "--disable-slash-commands", "-p"]
    for flag, value in [("--tools", "Read,Write,Edit,Glob,Grep"), ("--permission-mode", "acceptEdits"),
                        ("--permission-prompts", "none"), ("--add-dir", str(tmp_path / "in")),
                        ("--max-budget-usd", "0.2500"), ("--max-turns", "5"), ("--output-format", "stream-json"),
                        ("--model", "haiku")]:
        assert writer[writer.index(flag) + 1] == value
    assert "--verbose" in writer and "--no-session-persistence" in writer
    reader = r.argv(spec(ws, tmp_path, role="ask"))
    assert reader[reader.index("--permission-mode") + 1] == "dontAsk"
    assert reader[reader.index("--tools") + 1] == "Read,Glob,Grep"
    assert r.argv(spec(ws, tmp_path, role="compile"))[r.argv(spec(ws, tmp_path, role="compile")).index("--tools") + 1] == "Read,Edit"
    for argv in (writer, reader):
        assert not any("Bash" in a or "Web" in a for a in argv)
        assert "offline-test-placeholder" not in " ".join(argv)


def test_stream_parsing_and_tools():
    lines = [json.dumps({"type": "system", "subtype": "init", "tools": ["Read", "EndConversation"]}),
             "not json", json.dumps({"type": "assistant"}),
             json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.1,
                         "modelUsage": {"m": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3,
                                              "cacheCreationInputTokens": 4}}})]
    init, result = parse_stream(lines)
    assert init["tools"] == ["Read", "EndConversation"] and result["total_cost_usd"] == 0.1
    assert usage_of(result) == {"input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 3, "cache_write_tokens": 4}
    assert usage_of({"usage": {"input_tokens": 5, "cache_creation_input_tokens": 6}})["cache_write_tokens"] == 6
    assert tools_ok("ask", ["Read", "Glob", "Grep", "EndConversation"])
    assert not tools_ok("ask", ["Read", "Glob", "Grep", "Bash", "EndConversation"])
    assert not tools_ok("ask", ["Read", "EndConversation"])
    assert tools_ok("grader", ROLE_TOOLS["grader"])
    assert parse_version("2.1.319 (Claude Code)") == (2, 1, 319)


def test_run_saves_stream_and_ledger(ws, tmp_path):
    r = Runner(ws, ws.config())
    rr = r.run(spec(ws, tmp_path))
    assert rr.ok and rr.cost_usd == 0.01 and rr.session_id
    assert (ws.runs / "r1" / "t1.jsonl").exists()
    assert json.loads((ws.runs / "r1" / "t1.cmd.json").read_text())["argv"][0].endswith("claude")
    line = Ledger(ws.ledger_path).entries()[-1]
    assert line["outcome"] == "success" and line["cost_usd"] == 0.01 and line["cache_read_tokens"] == 1000
    assert oct(os.stat(ws.runs).st_mode & 0o777) == "0o700"
    assert "offline-test-placeholder" not in (ws.runs / "r1" / "t1.jsonl").read_text()


@pytest.mark.parametrize("mode,outcome,full_cap,retryable", [
    ("fail", "error_during_execution", False, True),
    ("noresult", "no_result", True, True),
    ("overcap", "error_max_budget_usd", False, False),
    ("maxturns", "error_max_turns", False, False),
    ("badtools", "bad_tools", True, False),
])
def test_run_failures(ws, tmp_path, monkeypatch, mode, outcome, full_cap, retryable):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", mode)
    rr = Runner(ws, ws.config()).run(spec(ws, tmp_path))
    assert rr.outcome == outcome and rr.charged_full_cap == full_cap and rr.retryable == retryable
    line = Ledger(ws.ledger_path).entries()[-1]
    assert line["outcome"] == outcome
    if full_cap:
        assert line["cost_usd"] == pytest.approx(0.25) and line["charged_full_cap"] is True
    if mode == "overcap":
        assert line["cost_usd"] == pytest.approx(0.3)  # spend can pass the run's cap


def test_timeout_sends_sigint(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_MISBEHAVE", "hang")
    rr = Runner(ws, ws.config()).run(spec(ws, tmp_path, timeout_s=1))
    assert rr.outcome == "timeout" and rr.charged_full_cap


def test_version_and_key_checks(ws, monkeypatch):
    monkeypatch.setenv("WIKI_FAKE_VERSION", "2.1.100 (Claude Code)")
    with pytest.raises(WikiError, match="2.1.319 or later"):
        Runner(ws, ws.config()).check_version()
    monkeypatch.delenv("WIKI_FAKE_VERSION")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(WikiError, match="No API key"):
        Runner(ws, ws.config()).check_ready()


def test_env_passes_only_what_claude_needs(ws, monkeypatch):
    monkeypatch.setenv("SOME_OTHER_SECRET", "s3cret")
    env = Runner(ws, ws.config())._env()
    assert "SOME_OTHER_SECRET" not in env and env["ANTHROPIC_API_KEY"] == "offline-test-placeholder"


def test_concurrency_limit(ws, tmp_path):
    import threading

    r = Runner(ws, ws.config(), concurrency=2)
    assert r.slots._value == 2
    seen = []
    orig = r._execute

    def tracked(*a, **k):
        seen.append(r.slots._value)
        return orig(*a, **k)

    r._execute = tracked
    threads = [threading.Thread(target=r.run, args=(spec(ws, tmp_path, task_id=f"t{i}"),)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert min(seen) >= 0 and len(seen) == 4


def test_share_floor_keeps_the_pool():
    s = cost_mod.share({"extract": 0.34, "check": 0.43, "compile-01": 0.0025, "compile-02": 0.0025}, 6.0, 0.10,
                       floor=0.10)
    assert s["compile-01"] == pytest.approx(0.10) and s["compile-02"] == pytest.approx(0.10)
    assert sum(s.values()) == pytest.approx(5.4)
    assert s["check"] / s["extract"] == pytest.approx(0.43 / 0.34)
    tiny = cost_mod.share({"a": 1.0, "b": 0.001}, 0.10, 0.10, floor=0.10)  # the pool can't give everyone the floor
    assert sum(tiny.values()) == pytest.approx(0.09)
