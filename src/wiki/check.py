"""wiki check: lint the wiki and enforce its invariants. The rules are SPEC.md's table, one function each."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import gitops
from .grammar import (
    PICTURE_MAX_LINES, RED_FLAGS, LinkResolver, current_picture, fact_tag, is_list_item, is_slug, json_list,
    lifecycle_only_added, links_in, parse_aliases, parse_sections, picture_text, plain_picture_lines,
    split_frontmatter, strip_lifecycle, tags_in, unquote,
)
from .workspace import sha256_bytes

PER_UNIT_DIRS = ("notes", "addenda", "contracts", "relations", "metrics", "open_questions")
TAGGED_DIRS = ("notes", "addenda", "contracts", "relations", "metrics")
ROOT_FILES = {"_schema.md", "_index.md", "_aliases.md", "README.md", "escalations.md"}
ROOT_STEMS = {Path(p).stem for p in ROOT_FILES}
FACTS_PAGE = re.compile(r"^(?P<slug>[a-z0-9-]+)-facts-(?P<n>\d+)$")
ESCALATIONS_PAGE = re.compile(r"^escalations-(?P<n>\d+)\.md$")  # program-split continuation pages
MAX_PAGE = 100_000
NOTE_PAGE = 20_000


@dataclass(frozen=True)
class Finding:
    severity: str  # error, warning, note
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper():7} {self.path}: {self.message} [{self.rule}]"


def is_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def read_wiki(wiki_dir: Path) -> dict[str, str]:
    files = {}
    wiki_dir = Path(wiki_dir)
    for p in sorted(wiki_dir.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts):
            files[p.relative_to(wiki_dir).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return files


def kind_of(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "root" if path in ROOT_FILES or ESCALATIONS_PAGE.match(path) else "other"
    if len(parts) == 2 and parts[0] in PER_UNIT_DIRS:
        return parts[0]
    if len(parts) == 2 and parts[0] == "entities":
        return "facts" if FACTS_PAGE.match(Path(path).stem) else "entity"
    return "other"


def heading_map(files: dict[str, str]) -> dict[tuple[str, str], dict]:
    """(unit, heading) -> {type, aliases, path, facts} for every notes and addenda heading (red-flags excluded)."""
    out = {}
    for path, text in files.items():
        k = kind_of(path)
        if k not in ("notes", "addenda"):
            continue
        unit = Path(path).stem
        for s in parse_sections(text):
            if s.heading == RED_FLAGS:
                continue
            key = (unit, s.heading)
            rec = out.setdefault(key, {"type": s.type, "aliases": [], "paths": [], "facts": []})
            rec["type"] = rec["type"] or s.type
            rec["aliases"] += [a for a in s.aliases if a not in rec["aliases"]]
            rec["paths"].append(path)
            rec["facts"] += s.facts
    return out


def run_checks(wiki_dir: Path, manifest, repo: Path | None = None, against: str | None = None,
               corpus_bytes: int | None = None) -> list[Finding]:
    files = read_wiki(wiki_dir)
    f: list[Finding] = []
    units = manifest.unit_names()
    add = lambda sev, rule, path, msg: f.append(Finding(sev, rule, path, msg))  # noqa: E731

    rows, alias_problems = parse_aliases(files.get("_aliases.md", ""))
    slugs = {r.slug for r in rows}
    # a page named for a row's slug is that entity's page, even when the slug ends like a fact page's name
    kinds = {p: "entity" if kind_of(p) == "facts" and Path(p).stem in slugs else kind_of(p) for p in files}
    entity_pages = {Path(p).stem for p in files if kinds[p] == "entity"}
    facts_pages = {Path(p).stem for p in files if kinds[p] == "facts"}
    link_targets = entity_pages | facts_pages | ROOT_STEMS | {Path(p).stem for p in files if ESCALATIONS_PAGE.match(p)}
    headings = heading_map(files)
    resolve = LinkResolver(rows, headings)
    via_alias = 0  # links in per-unit files that name a proposed heading and reach their page through _aliases.md

    for path, text in files.items():
        k = kinds[path]
        stem = Path(path).stem
        size = len(text.encode())
        # frontmatter
        fm, body, has = split_frontmatter(text)
        if not has:
            add("error", "frontmatter", path, "missing frontmatter")
        elif unquote(fm.get("name", "")) != stem:
            add("error", "frontmatter", path, f"name {fm.get('name')!r} differs from the file stem {stem!r}")
        elif not fm.get("description"):
            add("error", "frontmatter", path, "frontmatter has no description")
        if k == "entity" and has and json_list(fm.get("aliases", "[]")) is None:
            add("error", "frontmatter", path, "entity page aliases is not a JSON array")
        if k == "other":
            add("warning", "layout", path, "file outside the wiki layout")
        # per-unit file names
        if k in PER_UNIT_DIRS and stem not in units:
            add("error", "unit", path, f"no unit {stem!r} in the manifest")
        # fact lines and tags
        if k in TAGGED_DIRS or k in ("entity", "facts"):
            first = len(text.splitlines()) - len(body.splitlines()) + 1  # the file's line numbers, as the other rules give
            for n, line in enumerate(body.splitlines(), start=first):
                good, bad = tags_in(line)
                for snippet in bad:
                    add("error", "tag", path, f"line {n}: malformed source tag {snippet[:80]!r}")
                for t in good:
                    if t["unit"] not in units:
                        add("error", "tag", path, f"line {n}: tag names unit {t['unit']!r}, not in the manifest")
                    if t["section"] and k != "contracts":
                        add("error", "tag", path, f"line {n}: a tag has a section only in contracts/")
                    for key in ("as_of", "valid_from", "valid_to", "retracted"):
                        if t[key] and not is_date(t[key]):
                            add("error", "tag", path, f"line {n}: {t[key]!r} is not a date")
                if is_list_item(line) and not fact_tag(line) and not bad:
                    add("error", "tag", path, f"line {n}: fact line without a valid source tag: {line.strip()[:80]!r}")
            if k == "contracts":
                pass  # tags on their own line after a quoted provision are covered by tags_in above
        # sizes
        if size > MAX_PAGE:
            add("error", "size", path, f"{size:,} bytes, over the 100 KB limit")
        elif size > NOTE_PAGE:
            add("note", "size", path, f"{size:,} bytes, over 20 KB")
        # links: a target is a page; in a per-unit file it may also be a heading _aliases.md maps to one page
        if k != "open_questions" and path != "_schema.md":
            unit = stem if k in PER_UNIT_DIRS else None
            for target in links_in(body):
                if target in link_targets:
                    continue
                if unit is not None and resolve(target, unit) in entity_pages:
                    via_alias += 1
                    continue
                add("warning", "link", path, f"link to missing page [[{target}]]")
        # the Current picture: fact and CONFLICT lines only, at most PICTURE_MAX_LINES of them
        if k == "entity" and current_picture(text):
            plain = plain_picture_lines(text)
            if plain:
                add("warning", "picture", path, f"current picture has {len(plain)} line(s) that are not list items, "
                                                f"e.g. {plain[0].strip()[:60]!r}")
            n_lines = len([ln for ln in picture_text(text).splitlines() if ln.strip()])
            if n_lines > PICTURE_MAX_LINES:
                add("warning", "picture", path, f"current picture is {n_lines} lines, over {PICTURE_MAX_LINES}")
        # notes sections
        if k in ("notes", "addenda"):
            for s in parse_sections(text):
                for problem in s.problems:
                    add("error", "notes", path, f"line {s.line}: {problem}")
                if k == "notes" and s.heading != RED_FLAGS and (s.type is None):
                    add("error", "notes", path, f"line {s.line}: section {s.heading!r} has no type: line")

    # schema
    schema = files.get("_schema.md")
    want = manifest.data.get("schema_sha256")
    if schema is None:
        add("error", "schema", "_schema.md", "missing")
    elif want and sha256_bytes(schema.encode()) != want:
        add("error", "schema", "_schema.md", "changed since wiki init")

    if via_alias:
        add("note", "link", "(wiki)", f"{via_alias} link(s) name a proposed heading; each reaches its page through "
                                      "_aliases.md")

    # aliases
    for p in alias_problems:
        add("error", "aliases", "_aliases.md", p)
    mapped_slugs: set[str] = set()
    for (unit, heading), rec in sorted(headings.items()):
        hits = []
        for r in rows:
            if heading in r.proposed or f"{unit}#{heading}" in r.proposed:
                hits.append(r.slug)
        where = rec["paths"][0]
        if not hits:
            add("error", "aliases", where, f"heading {heading!r} is not mapped in _aliases.md")
        elif len(hits) > 1:
            add("error", "aliases", where, f"heading {heading!r} is mapped by {len(hits)} rows: {', '.join(hits)}")
        elif rec["facts"]:
            mapped_slugs.add(hits[0])
    row_slugs = [r.slug for r in rows]
    for s in {x for x in row_slugs if row_slugs.count(x) > 1}:
        add("error", "aliases", "_aliases.md", f"slug {s!r} has more than one row")
    claimed: dict[str, set[str]] = {}
    for r in rows:
        for name in r.names:
            claimed.setdefault(name.strip().lower(), set()).add(r.slug)
    for name, slugs in sorted(claimed.items()):
        if len(slugs) > 1:
            add("warning", "aliases", "_aliases.md", f"alias {name!r} claimed by {', '.join(sorted(slugs))}")

    # slugs and pages
    for s in sorted(mapped_slugs - entity_pages):
        add("error", "pages", f"entities/{s}.md", f"slug {s!r} has facts but no page")
    for s in sorted(entity_pages - set(row_slugs)):
        add("error", "pages", f"entities/{s}.md", "entity page without a slug in _aliases.md")
    for s in sorted(facts_pages):
        base = FACTS_PAGE.match(s).group("slug")
        if base not in entity_pages:
            add("error", "pages", f"entities/{s}.md", f"fact page without its entity page {base!r}")

    # index
    index = files.get("_index.md")
    if index is None:
        if entity_pages or units:
            add("error", "index", "_index.md", "missing")
    else:
        _, ibody, _ = split_frontmatter(index)
        linked = set(links_in(ibody))
        for s in sorted(entity_pages - linked):
            add("error", "index", "_index.md", f"entity page [[{s}]] is not in the index")
        for u in manifest.live_units():
            if not re.search(r"(?<![a-z0-9-])" + re.escape(u) + r"(?![a-z0-9]|-[a-z0-9])", ibody):
                add("error", "index", "_index.md", f"unit {u!r} is not in the index")
        for t in sorted(linked - link_targets):
            add("error", "index", "_index.md", f"index entry [[{t}]] has no target")
        for m in re.finditer(r"(?<![\w/])((?:notes|addenda|contracts|relations|metrics|open_questions|entities)/[a-z0-9-]+\.md)",
                             ibody):
            if m.group(1) not in files:
                add("error", "index", "_index.md", f"index entry {m.group(1)} has no target")

    # size of the whole wiki
    total = sum(len(t.encode()) for t in files.values())
    corpus = corpus_bytes if corpus_bytes is not None else manifest.corpus_bytes()
    if corpus and total > corpus:
        add("warning", "size", "(wiki)", f"wiki is {total:,} bytes, larger than its corpus ({corpus:,} bytes)")

    if against:
        f += check_against(files, repo, against)
    return f


def check_against(files: dict[str, str], repo: Path, ref: str) -> list[Finding]:
    """Every fact line in the base's per-unit files is still there, unchanged or with only valid_to or retracted added."""
    out = []
    if not gitops.ref_exists(repo, ref):
        return [Finding("error", "against", ref, "no such commit")]
    for rel, blob in gitops.tree_blobs(repo, ref, "wiki").items():
        path = rel[len("wiki/"):]
        if kind_of(path) not in TAGGED_DIRS:
            continue
        current = files.get(path)
        if current is None:
            out.append(Finding("error", "against", path, "file deleted since the base"))
            continue
        if gitops.blob_id(current.encode(), blob) == blob:
            continue  # the file is as it was at the base, so no line of it is lost: no need to read the base's copy
        base_text = gitops.show(repo, ref, rel) or ""
        for n, line in lost_lines(base_text, current):
            out.append(Finding("error", "against", path,
                               f"base line {n} missing or changed beyond valid_to/retracted: {line.strip()[:80]!r}"))
    return out


def _filed(line: str) -> bool:
    return bool(fact_tag(line) or line.lstrip().startswith("[source:"))


def lost_lines(base_text: str, current_text: str) -> list[tuple[int, str]]:
    """(line number, line) for each filed line of base_text that current_text no longer holds, either unchanged or
    with only valid_to or retracted added. Lines may be added and moved, and each base line needs a current line
    of its own: identical lines are paired first, then the rest by a maximum matching, so the order lines are
    compared in never decides what counts as lost."""
    base = [(n, ln) for n, ln in enumerate(base_text.splitlines(), start=1) if _filed(ln)]
    room: dict[str, int] = {}  # current lines by their exact text: how many there are
    for c in current_text.splitlines():
        if _filed(c):
            room[c.rstrip()] = room.get(c.rstrip(), 0) + 1
    # Identical lines are many copies of one text, so the matching runs on texts: held[c][b] is how many current
    # lines of text c hold base lines of text b. Augmenting paths are searched breadth-first, without recursion.
    held: dict[str, dict[str, int]] = {c: {} for c in room}
    used = dict.fromkeys(room, 0)
    by_key: dict[str, list[str]] = {}  # current texts by their text without lifecycle marks
    for c in room:
        by_key.setdefault(strip_lifecycle(c), []).append(c)
    # what comparing a base text with each current text of its group gave (0 not yet, 1 fits, 2 does not): a byte
    # per pair, made for each base text a search starts from or reaches, so no pair is compared twice and a page
    # of many lines that differ only in their dates stays small
    marks: dict[str, bytearray] = {}

    def fits(b: str, i: int, c: str) -> bool:  # c is text i of b's group in by_key
        m = marks.get(b)
        if m is None:
            m = marks[b] = bytearray(len(by_key[strip_lifecycle(b)]))
        if not m[i]:
            m[i] = 1 if lifecycle_only_added(b, c) else 2
        return m[i] == 1

    rest = []
    for n, line in base:
        text = line.rstrip()
        if used.get(text, 0) < room.get(text, 0):  # an unchanged line is always safe to pair
            used[text] += 1
            held[text][text] = held[text].get(text, 0) + 1
        else:
            rest.append((n, line))

    def take(b0: str) -> bool:
        """Pair one more base line of text b0, moving earlier pairs along an augmenting path if needed."""
        prev: dict[tuple[str, str], tuple[str, str] | None] = {("b", b0): None}
        queue = [("b", b0)]
        for node in queue:
            if node[0] == "b":
                for i, c in enumerate(by_key.get(strip_lifecycle(node[1]), [])):
                    if ("c", c) in prev or not fits(node[1], i, c):
                        continue
                    prev[("c", c)] = node
                    if used[c] < room[c]:
                        used[c] += 1
                        step: tuple[str, str] = ("c", c)
                        while (back := prev[step]) is not None:
                            if back[0] == "b":  # back's text now holds step's current line
                                held[step[1]][back[1]] = held[step[1]].get(back[1], 0) + 1
                            else:  # step's text gives up back's current line
                                held[back[1]][step[1]] -= 1
                            step = back
                        return True
                    queue.append(("c", c))
            else:
                for b, k in held[node[1]].items():
                    if k > 0 and ("b", b) not in prev:
                        prev[("b", b)] = node
                        queue.append(("b", b))
        return False

    # A text whose search failed stays unpaired: pairing other lines never opens a path for it. So its other copies
    # are lost without a search, and a page of many copies of one dropped line takes no longer than one copy.
    dead: set[str] = set()
    lost = []
    for n, line in rest:
        text = line.rstrip()
        if text in dead or not take(text):
            dead.add(text)
            lost.append((n, line))
    return lost


def is_slug_ok(s: str) -> bool:
    return is_slug(s)


def errors(findings: list[Finding]) -> list[Finding]:
    return [x for x in findings if x.severity == "error"]


def report(findings: list[Finding], show: int = 200) -> str:
    order = {"error": 0, "warning": 1, "note": 2}
    fs = sorted(findings, key=lambda x: (order[x.severity], x.path, x.message))
    counts = {s: sum(1 for x in fs if x.severity == s) for s in order}
    lines = [str(x) for x in fs[:show]]
    if len(fs) > show:
        lines.append(f"... {len(fs) - show} more")
    lines.append(f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['note']} note(s)")
    return "\n".join(lines)
