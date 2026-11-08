import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wiki.workspace import init_workspace  # noqa: E402

FAKE = Path(__file__).with_name("fake_claude.py")

ALPHA = """[SOURCE: SEC EDGAR | form: 8-K | filed: 2024-05-13 | accession: 0000000000-24-000001]

Acme, Inc. ("the Company") entered into a merger agreement with Beta Capital.
ENTITY: acme | company | Acme, Inc.; the Company
ENTITY: beta-capital | fund | Beta Capital
FACT: acme | Acme agreed to be acquired for $44.00 per share in cash.
FACT: beta-capital | Beta Capital funds will acquire Acme.
RELATION: beta-capital | acquires | acme
METRIC: Adjusted EBITDA | $235.4M | company definition | FY2023
QUESTION: Does the merger trigger the credit agreement's change-of-control clause?
REDFLAG: A director sold shares while the deal was pending.
CONTRACT: 2.1 | Merger consideration | Each share converts into the right to receive $44.00 in cash.
"""

BETA = """# Analyst note

Beta Capital raised the price for Acme.
ENTITY: acme | company | Acme; Project Falcon
FACT: acme | The price was raised to $46.50 per share.
ENTITY: gamma-lenders | lender | Gamma Lenders
FACT: gamma-lenders | Gamma Lenders provide a $2.3B term loan.
"""


def gamma_text(parts_hint: int = 3) -> str:
    """A long document: with batch_chars 3000 it splits into parts, each carrying its own markers."""
    blocks = []
    for i in range(parts_hint):
        blocks.append(
            f"ENTITY: delta-holdings | company | Delta Holdings\n"
            f"FACT: delta-holdings | Delta Holdings reported revenue segment {i} of $1{i}0M.\n"
            + ("Filler text about the filing and its many tables. " * 50 + "\n")
        )
    return "[SOURCE: SEC EDGAR | form: 10-K | filed: 2024-02-28]\n\n" + "".join(blocks)


@pytest.fixture(autouse=True)
def fake_claude(request, tmp_path, monkeypatch):
    """Every test runs against the fake claude, never the real one (live tests excepted)."""
    if request.node.get_closest_marker("live"):
        return None
    wrapper = tmp_path / "bin" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "fake-state"
    state.mkdir()
    monkeypatch.setenv("WIKI_CLAUDE_BIN", str(wrapper))
    monkeypatch.setenv("WIKI_FAKE_STATE", str(state))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-placeholder")
    for k in ("WIKI_WORKSPACE", "WIKI_FAKE_MISBEHAVE", "WIKI_FAKE_COST", "WIKI_FAKE_VERSION"):
        monkeypatch.delenv(k, raising=False)
    return wrapper


def set_runner(ws, **values):
    text = ws.toml.read_text()
    for k, v in values.items():
        import re

        text = re.sub(rf"^{k} = .*$", f"{k} = {v}", text, count=1, flags=re.M)
    ws.toml.write_text(text)


@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "alpha_8k.txt").write_text(ALPHA)
    (d / "beta_note.md").write_text(BETA)
    (d / "gamma_10k.txt").write_text(gamma_text())
    return d


@pytest.fixture
def ws(tmp_path):
    w = init_workspace(tmp_path / "ws")
    set_runner(w, batch_chars=3000, concurrency=2)
    return w


@pytest.fixture
def loaded(ws, corpus):
    from wiki.documents import add

    add(ws, [corpus])
    return ws
