"""manifest.json: every unit with its document, part, sha256, size, batch and status; splitting and batching."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from . import WikiError
from .grammar import slugify
from .workspace import sha256_bytes

# Unit statuses: new, changed (need extraction), extracted, checked, filed (built), failed, removed.
PENDING = {"new", "changed", "extracted", "checked", "failed"}


def doc_slug(path_or_name: str) -> str:
    """A document's slug: its file name without extension, every run of other characters one hyphen."""
    name = Path(path_or_name).name
    stem = name[: -len(Path(name).suffix)] if Path(name).suffix else name
    return slugify(stem)


def split_units(slug: str, text: str, batch_chars: int) -> list[tuple[str, int | None, int, str]]:
    """[(unit slug, part or None, parts, text)]. A document longer than batch_chars becomes parts, each
    keeping the document's [SOURCE] line plus "part n of N", split at line boundaries."""
    if len(text) <= batch_chars:
        return [(slug, None, 1, text)]
    first, _, body = text.partition("\n")
    header = first[:-1] if first.startswith("[SOURCE:") and first.rstrip().endswith("]") else f"[SOURCE: {slug}"
    header = header.rstrip().rstrip("]")
    body = body.lstrip("\n")
    room = batch_chars - len(header) - 40
    pieces, cur, size = [], [], 0
    for line in body.splitlines(keepends=True):
        while len(line) > room:  # a single very long line is cut
            if cur:
                pieces.append("".join(cur))
                cur, size = [], 0
            pieces.append(line[:room])
            line = line[room:]
        if size + len(line) > room and cur:
            pieces.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        pieces.append("".join(cur))
    n = len(pieces)
    return [
        (f"{slug}-part{i}", i, n, f"{header} | part {i} of {n}]\n\n{piece}")
        for i, piece in enumerate(pieces, 1)
    ]


class Manifest:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def new(cls, schema_sha256: str) -> "Manifest":
        return cls({"version": 1, "schema_sha256": schema_sha256, "documents": {}, "units": {}, "entities": {}})

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls(json.loads(Path(path).read_text()))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")

    @property
    def units(self) -> dict:
        return self.data["units"]

    @property
    def documents(self) -> dict:
        return self.data["documents"]

    @property
    def entities(self) -> dict:
        return self.data.setdefault("entities", {})

    def unit_names(self) -> set[str]:
        return set(self.units)

    def live_units(self) -> list[str]:
        return sorted(u for u, rec in self.units.items() if rec.get("status") != "removed")

    def pending_units(self) -> list[str]:
        return sorted(u for u, rec in self.units.items() if rec.get("status") in PENDING)

    def corpus_bytes(self) -> int:
        return sum(int(rec.get("size", 0)) for rec in self.units.values() if rec.get("status") != "removed")

    def add_document(self, source: str, text: str, docs_dir: Path, batch_chars: int) -> list[tuple[str, str]]:
        """Record one converted document; write its units to docs/. Returns [(unit, status)]."""
        slug = doc_slug(source)
        doc_sha = sha256_bytes(text.encode())
        prev = self.documents.get(slug)
        if prev and prev["source"] != Path(source).name:
            raise WikiError(
                f"{Path(source).name} and {prev['source']} both make the slug {slug!r}; rename one of them."
            )
        if prev and prev["sha256"] == doc_sha:
            return [(u, "unchanged") for u in prev["units"]]
        parts = split_units(slug, text, batch_chars)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        results = []
        new_units = []
        for unit, part, n, unit_text in parts:
            other = self.units.get(unit)
            if other and other["document"] != slug:
                raise WikiError(f"Unit slug {unit!r} of {source} collides with a unit of {other['document']}.")
            data = unit_text.encode()
            sha = sha256_bytes(data)
            status = "new" if not other else ("unchanged" if other["sha256"] == sha else "changed")
            target = Path(docs_dir) / f"{unit}.txt"
            if status != "unchanged" or not target.exists():
                if target.exists():
                    os.chmod(target, 0o600)
                    target.unlink()
                target.write_bytes(data)
                os.chmod(target, 0o444)
            rec = {
                "document": slug, "part": part, "parts": n, "sha256": sha, "size": len(data),
                "batch": (other or {}).get("batch"), "status": (other or {}).get("status", "new"),
                "added": now,
            }
            if status in ("new", "changed"):
                rec["status"] = status
            self.units[unit] = rec
            new_units.append(unit)
            results.append((unit, status))
        for old in (prev or {}).get("units", []):
            if old not in new_units:  # fewer parts than before: kept so its citations stay valid
                self.units[old]["status"] = "removed"
                results.append((old, "removed"))
        self.documents[slug] = {"source": Path(source).name, "sha256": doc_sha, "size": len(text.encode()),
                                "units": new_units, "added": now}
        return results

    def set_status(self, units, status: str) -> None:
        for u in units:
            if u in self.units:
                self.units[u]["status"] = status


def make_batches(units: list[str], sizes: dict[str, int], batch_chars: int) -> dict[str, list[str]]:
    """Pack units into batches of about batch_chars; a unit as large as a batch gets its own."""
    batches: dict[str, list[str]] = {}
    cur: list[str] = []
    size = 0
    n = 0
    for u in units:
        s = sizes[u]
        if cur and size + s > batch_chars:
            n += 1
            batches[f"b{n:02d}"] = cur
            cur, size = [], 0
        cur.append(u)
        size += s
    if cur:
        n += 1
        batches[f"b{n:02d}"] = cur
    return batches
