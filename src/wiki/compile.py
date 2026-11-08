"""The program's half of Compile: write each entities/<slug>.md from its fact lines, grouped by unit.

A Compile run may then change only the text between a page's current-picture markers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .check import ESCALATIONS_PAGE, FACTS_PAGE, heading_map, kind_of, read_wiki
from .grammar import MARK_END, MARK_START, current_picture, frontmatter, parse_aliases, parse_sections

SPLIT_AT = 80_000
FACTS_PAGE_TARGET = 70_000


@dataclass
class CompileResult:
    written: list[str] = field(default_factory=list)  # slugs whose page text changed
    removed: list[str] = field(default_factory=list)
    needs_picture: list[str] = field(default_factory=list)  # slugs that want a Compile run
    facts_sha: dict[str, str] = field(default_factory=dict)


def collect(files: dict[str, str]) -> tuple[dict, dict]:
    """(slug -> {"row", "groups": {source path: [lines]}}, problems) from _aliases.md and the source layer."""
    rows, _ = parse_aliases(files.get("_aliases.md", ""))
    by_slug = {r.slug: {"row": r, "groups": {}, "types": set()} for r in rows}
    headings = heading_map(files)
    for (unit, heading), rec in sorted(headings.items()):
        hits = [r.slug for r in rows if heading in r.proposed or f"{unit}#{heading}" in r.proposed]
        if len(hits) != 1:
            continue  # wiki check reports it
        entry = by_slug[hits[0]]
        if rec["type"]:
            entry["types"].add(rec["type"])
        for path in sorted(set(rec["paths"]), key=lambda p: (0 if p.startswith("notes/") else 1, p)):
            for s in parse_sections(files[path]):
                if s.heading == heading and s.facts:
                    lines = entry["groups"].setdefault(unit, [])
                    lines.extend(x for x in s.facts if x not in lines)
    for path, text in sorted(files.items()):
        if kind_of(path) != "relations":
            continue
        for line in text.splitlines():
            for slug in by_slug:
                if f"[[{slug}]]" in line and line.lstrip().startswith("- ") and by_slug[slug]["groups"]:
                    entry = by_slug[slug]
                    lines = entry["groups"].setdefault(Path(path).stem, [])
                    if line.rstrip() not in lines:
                        lines.append(line.rstrip())
    return by_slug, {}


def _sha(entry: dict) -> str:
    r = entry["row"]
    payload = json.dumps([r.type, r.names, r.description, sorted(entry["groups"].items())], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def render_page(slug: str, entry: dict, picture: str) -> tuple[str, dict[str, str]]:
    """(main page, {facts page stem: text})."""
    r = entry["row"]
    desc = (r.description or f"{r.type or 'entity'} {slug}").replace("\n", " ").strip()
    fm = frontmatter({
        "name": slug,
        "description": desc,
        "aliases": json.dumps(r.names, ensure_ascii=False),
        "type": r.type or "entity",
    })
    picture = picture.strip("\n")
    head = (f"{fm}# {slug}\n\n{MARK_START}\n" + (picture + "\n" if picture else "") + f"{MARK_END}\n\n"
            "Compiled by wiki build from the source layer: change notes/, addenda/ or _aliases.md, not this page.\n")
    sections = []
    for unit in sorted(entry["groups"]):
        sections.append(f"\n## From {unit}\n" + "\n".join(entry["groups"][unit]) + "\n")
    full = head + "".join(sections)
    if len(full.encode()) <= SPLIT_AT:
        return full, {}
    pages: list[list[str]] = [[]]
    size = 0
    for unit in sorted(entry["groups"]):
        heading = f"\n## From {unit}\n"
        open_heading = False
        for line in entry["groups"][unit]:
            n = len(line.encode()) + 1
            if pages[-1] and size + n + len(heading) > FACTS_PAGE_TARGET:
                pages.append([])
                size = 0
                open_heading = False
            if not open_heading:
                pages[-1].append(heading)
                size += len(heading)
                open_heading = True
            pages[-1].append(line + "\n")
            size += n
    extra = {}
    names = []
    for i, secs in enumerate(pages, 1):
        stem = f"{slug}-facts-{i}"
        names.append(stem)
        extra[stem] = frontmatter({"name": stem, "description": f"fact lines of {slug}, part {i} of {len(pages)}"}) + \
            f"# {slug}: facts, part {i} of {len(pages)}\n\nBack to [[{slug}]].\n" + "".join(secs)
    main = head + "\nThe fact lines are on " + ", ".join(f"[[{n}]]" for n in names) + ".\n"
    return main, extra


def compile_pages(wiki_dir: Path, known_sha: dict[str, str], only: set[str] | None = None) -> CompileResult:
    """Write every entity page from its facts; drop pages of slugs left with no facts."""
    wiki_dir = Path(wiki_dir)
    files = read_wiki(wiki_dir)
    by_slug, _ = collect(files)
    res = CompileResult()
    ent_dir = wiki_dir / "entities"
    ent_dir.mkdir(exist_ok=True)
    existing = {Path(p).stem for p in files if kind_of(p) in ("entity", "facts")}
    keep: set[str] = set()
    for slug, entry in sorted(by_slug.items()):
        if not entry["groups"]:
            continue
        sha = _sha(entry)
        res.facts_sha[slug] = sha
        path = ent_dir / f"{slug}.md"
        old = path.read_text() if path.exists() else ""
        span = current_picture(old)
        picture = old[span[0]:span[1]] if span else ""
        main, extra = render_page(slug, entry, picture)
        keep.add(slug)
        keep |= set(extra)
        if main != old:
            path.write_text(main)
            res.written.append(slug)
        for stem, text in extra.items():
            p = ent_dir / f"{stem}.md"
            if not p.exists() or p.read_text() != text:
                p.write_text(text)
        if (known_sha.get(slug) != sha or not picture.strip()) and (only is None or slug in only):
            res.needs_picture.append(slug)
    for stem in sorted(existing - keep):
        m = FACTS_PAGE.match(stem)
        if m and m.group("slug") in keep and stem in keep:
            continue
        (ent_dir / f"{stem}.md").unlink(missing_ok=True)
        if not FACTS_PAGE.match(stem):
            res.removed.append(stem)
    return res


def picture_lines(text: str) -> int:
    span = current_picture(text)
    if not span:
        return 0
    return len([ln for ln in text[span[0]:span[1]].splitlines() if ln.strip()])


ESCALATIONS_BUDGET = 78_000  # bytes per escalations page; the hard page limit is 100 KB


def split_escalations(wiki_dir: Path, budget: int = ESCALATIONS_BUDGET) -> list[str]:
    """Keep escalations.md under the page limit: the digest stays whole on escalations.md, and the full entries
    (blocks that start with `### `) that do not fit move, in order, to escalations-2.md, escalations-3.md, ...

    The program owns the continuation pages, as it owns entity fact pages. Returns the paths it wrote or removed.
    """
    from .grammar import split_frontmatter

    wiki_dir = Path(wiki_dir)
    main = wiki_dir / "escalations.md"
    old = sorted(p for p in wiki_dir.glob("escalations-*.md") if ESCALATIONS_PAGE.match(p.name))
    if not main.exists():
        return []
    text = main.read_text()
    # an earlier split: put the continued entries back before splitting again
    fm, body, has = split_frontmatter(text)
    if old:
        body = body.split(CONTINUED_MARK)[0]
        for p in sorted(old, key=lambda q: int(ESCALATIONS_PAGE.match(q.name)["n"])):
            _, more, _ = split_frontmatter(p.read_text())
            body = body.rstrip("\n") + "\n\n" + more.split("\n", 1)[1].lstrip("\n")
    head_fm = text[: len(text) - len(split_frontmatter(text)[1])] if has else ""
    if len((head_fm + body).encode()) <= budget:
        if not old:
            return []
        main.write_text(head_fm + body)
        for p in old:
            p.unlink()
        return ["escalations.md"] + [p.name for p in old]
    lines = body.splitlines(keepends=True)
    first = next((i for i, ln in enumerate(lines) if ln.startswith("### ")), len(lines))
    head, blocks = "".join(lines[:first]), []
    for ln in lines[first:]:
        if ln.startswith("### ") or not blocks:
            blocks.append(ln)
        else:
            blocks[-1] += ln
    pages: list[list[str]] = [[]]
    used = len((head_fm + head).encode()) + 400
    for b in blocks:
        size = len(b.encode())
        if pages[-1] and used + size > budget:
            pages.append([])
            used = 400
        pages[-1].append(b)
        used += size
    written = []
    names = ["escalations"] + [f"escalations-{n}" for n in range(2, len(pages) + 1)]
    pointer = "Full entries continue on " + ", ".join(f"[[{n}]]" for n in names[1:]) + ", in rank order.\n"
    main.write_text(head_fm + head + "".join(pages[0]).rstrip("\n") + "\n\n" + CONTINUED_MARK + "\n" + pointer)
    written.append("escalations.md")
    for n, (name, page) in enumerate(zip(names[1:], pages[1:]), start=2):
        nxt = f" Continued on [[{names[n]}]]." if n < len(names) else ""
        (wiki_dir / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: Ranked red flags, continued: full entries, part {n} of {len(names)}, "
            f"written by the program from escalations.md\n---\n"
            f"Continued from [[{names[n - 2]}]]; the digest is on [[escalations]].{nxt}\n\n" + "".join(page))
        written.append(f"{name}.md")
    for p in old:
        if p.name not in written:
            p.unlink()
            written.append(p.name)
    return written


CONTINUED_MARK = "<!-- escalations:continued -->"
