"""wiki add and wiki fetch-example."""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import WikiError, gitops, tool_root
from .convert import SUPPORTED, convert, with_source_line
from .manifest import Manifest, doc_slug
from .workspace import sha256_bytes

ANALYST_NOTE_SOURCE = "[SOURCE: synthetic demo document (marked) | analyst flash note | as-of: 2024-05-14]"
CSV_SOURCE = "[SOURCE: synthetic demo document (marked) | vendor data extract | as-of: 2024-05-14]"


def expand(paths) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():  # nothing hidden: no dot-file, and no file under a dot-folder
            out += sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in SUPPORTED
                          and not any(part.startswith(".") for part in q.relative_to(p).parts))
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
    # Every refusal comes before anything is written: a file that cannot be converted, or two documents that make
    # one slug, used to stop the command with the earlier documents already replaced in docs/ and the manifest not
    # saved, so docs/ no longer matched it.
    seen: dict[str, Path] = {}
    for f in files:  # report.txt in two folders would be taken for two versions of one document
        slug = doc_slug((names or {}).get(str(f), f.name))
        if slug in seen and seen[slug].resolve() != f.resolve():
            raise WikiError(f"{seen[slug]} and {f} both make the slug {slug!r}; rename one of them.")
        seen[slug] = f
    converted = []
    for f in files:
        name = (names or {}).get(str(f), f.name)
        raw, line = convert(f), (source_lines or {}).get(str(f))
        text = with_source_line(raw, name, line)
        prev = manifest.documents.get(doc_slug(name))
        if prev and prev.get("units") and prev["sha256"] != sha256_bytes(text.encode()):
            # A document with no [SOURCE] line of its own was given "added: <that day>". Added again unchanged it
            # keeps that day: with today's, its text would differ and the next build would pay to file it again.
            stored = ws.docs / f"{prev['units'][0]}.txt"
            day = None
            if stored.exists():
                with open(stored, encoding="utf-8", errors="replace") as handle:
                    day = re.search(r"\| added: (\d{4}-\d{2}-\d{2})", handle.readline())
            if day:
                as_before = with_source_line(raw, name, line, today=day.group(1))
                if sha256_bytes(as_before.encode()) == prev["sha256"]:
                    text = as_before
        converted.append((name, text))
    trial = Manifest(copy.deepcopy(manifest.data))
    for name, text in converted:
        trial.add_document(name, text, ws.docs, batch_chars, write=False)
    try:
        for name, text in converted:
            for unit, status in manifest.add_document(name, text, ws.docs, batch_chars):
                counts[status] = counts.get(status, 0) + 1
                if status != "unchanged":
                    print(f"  {status:9} {unit}")
    finally:  # whatever stopped it, the manifest records what docs/ now holds
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
