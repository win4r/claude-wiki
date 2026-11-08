"""wiki add and wiki fetch-example."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import WikiError, gitops, tool_root
from .convert import SUPPORTED, convert, with_source_line
from .manifest import Manifest

ANALYST_NOTE_SOURCE = "[SOURCE: synthetic demo document (marked) | analyst flash note | as-of: 2024-05-14]"
CSV_SOURCE = "[SOURCE: synthetic demo document (marked) | vendor data extract | as-of: 2024-05-14]"


def expand(paths) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in SUPPORTED
                          and not q.name.startswith("."))
        elif p.exists():
            out.append(p)
        else:
            raise WikiError(f"{p}: no such file")
    return out


def add(ws, paths, source_lines: dict[str, str] | None = None, names: dict[str, str] | None = None) -> int:
    """Convert documents, store their units in docs/ and record them in manifest.json."""
    cfg = ws.config()
    files = expand(paths)
    if not files:
        raise WikiError("No documents to add.")
    manifest = Manifest.load(ws.manifest_path)
    batch_chars = int(cfg.runner("batch_chars", 120_000))
    counts: dict[str, int] = {}
    for f in files:
        text = convert(f)
        name = (names or {}).get(str(f), f.name)
        text = with_source_line(text, name, (source_lines or {}).get(str(f)))
        for unit, status in manifest.add_document(name, text, ws.docs, batch_chars):
            counts[status] = counts.get(status, 0) + 1
            if status != "unchanged":
                print(f"  {status:9} {unit}")
    manifest.save(ws.manifest_path)
    gitops.commit_all(ws.repo, f"wiki add: {len(files)} document(s)")
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    print(f"Added {len(files)} document(s): {summary}. Run wiki build to file new or changed units.")
    return 0


def fetch_example(ws, tier: str) -> int:
    if tier not in ("quickstart", "core"):
        raise WikiError("--tier must be quickstart or core")
    if not os.environ.get("EDGAR_USER_AGENT", "").strip():
        raise WikiError(
            "EDGAR_USER_AGENT is not set. SEC asks every client for a contact: set it to 'Your Name you@example.com'.")
    example = tool_root() / "examples" / "sec-deal"
    out = ws.runs / f"fetch-{tier}"
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "EDGAR_USER_AGENT", "LANG", "TMPDIR")}
    proc = subprocess.run([sys.executable, "-I", str(example / "fetch_data_room.py"), f"--tier={tier}",
                           f"--out={out}"], env=env)
    if proc.returncode != 0:
        raise WikiError("The fetch script failed; see its output above.")
    docs = sorted((out / "docs").glob("*.txt"))
    if not docs:
        raise WikiError("The fetch script wrote no documents.")
    staged = out / "synthetic"
    staged.mkdir(exist_ok=True)
    note = staged / "analyst_note.docx"
    csv = staged / "data_provider_extract.csv"
    shutil.copyfile(example / "analyst_note.docx", note)
    shutil.copyfile(example / "data_provider_extract.csv", csv)
    add(ws, [*docs, note, csv], source_lines={str(note): ANALYST_NOTE_SOURCE, str(csv): CSV_SOURCE})
    cfg = ws.config()
    cfg.set_tier(tier)
    ws.eval_dir.mkdir(exist_ok=True)
    for qs in example.glob("*-tier.toml"):
        shutil.copyfile(qs, ws.eval_dir / qs.name)
    gitops.commit_all(ws.repo, f"fetch-example: {tier} tier question sets")
    print(f"Workspace tier set to {tier} (build cap {cfg.build_cap():.2f} USD).")
    return 0
