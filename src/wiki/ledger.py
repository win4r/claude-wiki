"""ledger.jsonl: the only record of spend. One line per paid run, success or failure."""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

FIELDS = (
    "time", "command", "phase", "task_id", "model", "session_id",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cost_usd", "outcome",
)

_LOCK = threading.Lock()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, **entry) -> dict:
        line = {k: entry.get(k) for k in FIELDS}
        line["time"] = line["time"] or datetime.now(UTC).isoformat(timespec="seconds")
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            line[k] = int(line[k] or 0)
        line["cost_usd"] = round(float(line["cost_usd"] or 0.0), 6)
        extra = {k: v for k, v in entry.items() if k not in FIELDS and v is not None}
        line.update(extra)  # e.g. charged_full_cap, input_chars, run_cap_usd
        text = json.dumps(line, ensure_ascii=False) + "\n"
        with _LOCK:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(text)
        return line

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
        return out

    def spent(self) -> float:
        return sum(float(e.get("cost_usd") or 0) for e in self.entries())

    def spent_where(self, **match) -> float:
        return sum(
            float(e.get("cost_usd") or 0)
            for e in self.entries()
            if all(e.get(k) == v for k, v in match.items())
        )

    def summary(self) -> dict:
        by_command: dict[str, float] = defaultdict(float)
        by_phase: dict[tuple[str, str], float] = defaultdict(float)
        runs: dict[str, int] = defaultdict(int)
        for e in self.entries():
            c = float(e.get("cost_usd") or 0)
            by_command[e.get("command") or "?"] += c
            by_phase[(e.get("command") or "?", e.get("phase") or "-")] += c
            runs[e.get("command") or "?"] += 1
        return {"by_command": dict(by_command), "by_phase": dict(by_phase), "runs": dict(runs)}

    def averages(self) -> dict[tuple[str, str], dict]:
        """Per (phase, model): mean cost per run and per input character, over runs that ended with a result."""
        acc: dict[tuple[str, str], dict] = {}
        for e in self.entries():
            if e.get("charged_full_cap") or e.get("outcome") in ("no_result", "bad_tools"):
                continue
            key = (e.get("phase") or "", e.get("model") or "")
            a = acc.setdefault(key, {"n": 0, "cost": 0.0, "chars": 0, "n_chars": 0, "cost_chars": 0.0})
            a["n"] += 1
            a["cost"] += float(e.get("cost_usd") or 0)
            if e.get("input_chars"):
                a["chars"] += int(e["input_chars"])
                a["cost_chars"] += float(e.get("cost_usd") or 0)
                a["n_chars"] += 1
        out = {}
        for key, a in acc.items():
            out[key] = {
                "n": a["n"],
                "per_run": a["cost"] / a["n"],
                "per_char": (a["cost_chars"] / a["chars"]) if a["chars"] else None,
            }
        return out
