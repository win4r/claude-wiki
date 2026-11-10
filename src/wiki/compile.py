"""The program's half of Compile: write each entities/<slug>.md from its fact lines, grouped by unit.

A Compile run may then change only the text between a page's current-picture markers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .check import ESCALATIONS_PAGE, FACTS_PAGE, heading_map, kind_of, read_wiki
from .grammar import (
    MARK_END, MARK_START, TAG_START, LinkResolver, current_picture, frontmatter, is_list_item, links_in,
    parse_aliases, parse_sections, repoint_links, tags_in,
)

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
    by_slug = {r.slug: {"row": r, "groups": {}, "types": set(), "where": []} for r in rows}
    headings = heading_map(files)
    sections: dict[str, list] = {}  # each file parsed once, however many headings it holds
    for (unit, heading), rec in sorted(headings.items()):
        hits = [r.slug for r in rows if heading in r.proposed or f"{unit}#{heading}" in r.proposed]
        if len(hits) != 1:
            continue  # wiki check reports it
        entry = by_slug[hits[0]]
        if rec["type"]:
            entry["types"].add(rec["type"])
        for path in sorted(set(rec["paths"]), key=lambda p: (0 if p.startswith("notes/") else 1, p)):
            if path not in sections:
                sections[path] = parse_sections(files[path])
            for s in sections[path]:
                if s.heading == heading and s.facts:
                    lines = entry["groups"].setdefault(unit, [])
                    lines.extend(x for x in s.facts if x not in lines)
                    entry["where"].append((unit, path, f"`## {heading}`", len(s.facts)))
    # a relations line belongs to every entity it links, by slug or by a heading that _aliases.md maps to it
    resolve = LinkResolver(rows, headings)
    for path, text in sorted(files.items()):
        if kind_of(path) != "relations":
            continue
        unit = Path(path).stem
        for line in text.splitlines():
            if not line.lstrip().startswith("- "):
                continue
            named: dict[str, set[str]] = {}
            for target in links_in(line):
                slug = resolve(target, unit)
                if slug in by_slug and by_slug[slug]["groups"]:
                    named.setdefault(slug, set()).add(target)
            for slug, targets in named.items():
                entry = by_slug[slug]
                lines = entry["groups"].setdefault(unit, [])
                if line.rstrip() not in lines:
                    lines.append(line.rstrip())
                    rc = entry.setdefault("rel_counts", {})
                    rc[path] = rc.get(path, 0) + 1
                    entry.setdefault("rel_names", {}).setdefault(path, set()).update(targets)
    return by_slug, {}


def link_resolver(files: dict[str, str]) -> LinkResolver:
    return LinkResolver(parse_aliases(files.get("_aliases.md", ""))[0], heading_map(files))


def normalize_picture(picture: str, resolve: LinkResolver) -> str:
    """A Current picture as the program keeps it: list items, each `[[link]]` pointing at its entity's slug.

    Pictures written before entity pages moved their fact lines to the per-unit files end with a plain line naming
    the page's sections. Those sections are gone, so a plain line with no source tag is dropped; one that carries
    a tag is kept, for wiki check to report."""
    out = []
    for ln in picture.strip("\n").splitlines():
        if ln.strip() and not is_list_item(ln) and not TAG_START.search(ln):
            continue
        units = [t["unit"] for t in tags_in(ln)[0]]
        out.append(repoint_links(ln, lambda target, units=units: resolve.on_page(target, units)))
    return "\n".join(out).strip("\n")


def normalize_pictures(wiki_dir: Path, pages: list[str]) -> list[str]:
    """Normalize the Current picture of each page (paths under the wiki); returns the pages it changed."""
    wiki_dir = Path(wiki_dir)
    resolve = link_resolver(read_wiki(wiki_dir))
    changed = []
    for rel in pages:
        p = wiki_dir / rel
        if not p.exists():
            continue
        text = p.read_text()
        span = current_picture(text)
        if not span:
            continue
        new = normalize_picture(text[span[0]:span[1]], resolve)
        new_text = text[:span[0]] + "\n" + (new + "\n" if new else "") + text[span[1]:]
        if new_text != text:
            p.write_text(new_text)
            changed.append(rel)
    return changed


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
    rows_out = [f"| {u} | {path} | {where} | {n} |" for u, path, where, n in sorted(entry.get("where", []))]
    names = entry.get("rel_names", {})
    rows_out += [f"| {Path(path).stem} | {path} | lines naming "
                 + " or ".join(f"`[[{t}]]`" for t in sorted(names.get(path) or {slug})) + f" | {n} |"
                 for path, n in sorted(entry.get("rel_counts", {}).items())]
    sources = ("\n## Sources\n"
               "The fact lines about this entity stay in the per-unit files; open a file and go to the heading:\n\n"
               "| unit | file | where | facts |\n| --- | --- | --- | --- |\n" + "\n".join(rows_out) + "\n")
    return head + sources, {}


def compile_pages(wiki_dir: Path, known_sha: dict[str, str], only: set[str] | None = None) -> CompileResult:
    """Write every entity page from its facts; drop pages of slugs left with no facts."""
    wiki_dir = Path(wiki_dir)
    files = read_wiki(wiki_dir)
    by_slug, _ = collect(files)
    resolve = link_resolver(files)
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
        picture = normalize_picture(old[span[0]:span[1]], resolve) if span else ""
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
        if not FACTS_PAGE.match(stem) or stem in by_slug:
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
    # An earlier split: put the continued entries back before splitting again. A page Index has since written
    # whole holds every entry already; the old continuation pages are then stale, and are replaced or removed below.
    fm, body, has = split_frontmatter(text)
    if old and CONTINUED_MARK in body:  # the program's own split, still in place: put its entries back
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
    if not blocks:  # no `### ` entry to split at: the page stays as it is, for wiki check to report its size
        return []
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
