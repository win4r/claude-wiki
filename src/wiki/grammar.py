"""The wiki's grammars: slugs, source tags, fact lines, notes sections, the aliases table, frontmatter, links."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE = r"\d{4}-\d{2}-\d{2}"
UNIT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# Fields in this order: source, as-of, then optionally valid_from, valid_to, retracted, section.
TAG_RE = re.compile(
    r"\[source: (?P<unit>" + UNIT + r") \| as-of: (?P<as_of>" + DATE + r")"
    r"(?: \| valid_from: (?P<valid_from>" + DATE + r"))?"
    r"(?: \| valid_to: (?P<valid_to>" + DATE + r"))?"
    r"(?: \| retracted: (?P<retracted>" + DATE + r"))?"
    r"(?: \| section: (?P<section>[^\]|\n]+?))?"
    r"\]"
)
TAG_START = re.compile(r"\[source:", re.I)
TAG_AT_END = re.compile(r"(" + TAG_RE.pattern + r")\s*$")
LIFECYCLE = re.compile(r" \| (?:valid_to|retracted): " + DATE)
WIKILINK = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
CODE_SPAN = re.compile(r"`[^`\n]*`")
MARK_START = "<!-- current-picture:start -->"
MARK_END = "<!-- current-picture:end -->"
RED_FLAGS = "red-flags"


def slugify(name: str) -> str:
    """Lowercase letters, digits and single hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "untitled"


def is_slug(s: str) -> bool:
    return bool(SLUG_RE.match(s or ""))


def parse_tag(text: str) -> dict | None:
    m = TAG_RE.fullmatch(text.strip())
    return m.groupdict() if m else None


def is_list_item(line: str) -> bool:
    return line.lstrip().startswith(("- ", "* ")) and not line.strip() in ("-", "*")


def tags_in(line: str) -> tuple[list[dict], list[str]]:
    """(valid tags, malformed tag snippets) for every "[source:" in a line."""
    good, bad = [], []
    for m in TAG_START.finditer(line):
        tm = TAG_RE.match(line, m.start())
        if tm:
            good.append(tm.groupdict())
        else:
            end = line.find("]", m.start())
            bad.append(line[m.start(): end + 1 if end != -1 else len(line)])
    return good, bad


def fact_tag(line: str) -> dict | None:
    """The tag that ends a fact line, or None."""
    m = TAG_AT_END.search(line)
    return parse_tag(m.group(1)) if m else None


def strip_lifecycle(line: str) -> str:
    """A fact line with valid_to and retracted removed from its tags, for --against comparisons."""
    return LIFECYCLE.sub("", line.rstrip())


def lifecycle_only_added(base: str, current: str) -> bool:
    """True when current equals base, or base with valid_to and/or retracted added (never changed or removed)."""
    if base.rstrip() == current.rstrip():
        return True
    if strip_lifecycle(base) != strip_lifecycle(current):
        return False
    bt, ct = fact_tag(base) or {}, fact_tag(current) or {}
    for key in ("valid_to", "retracted"):
        if bt.get(key) and bt.get(key) != ct.get(key):
            return False
    return True


def tag(unit: str, as_of: str, **extra) -> str:
    parts = [f"source: {unit}", f"as-of: {as_of}"]
    for key in ("valid_from", "valid_to", "retracted", "section"):
        if extra.get(key):
            parts.append(f"{key}: {extra[key]}")
    return "[" + " | ".join(parts) + "]"


# --- frontmatter

def split_frontmatter(text: str) -> tuple[dict, str, bool]:
    """(fields, body, has_frontmatter). Raw string values; no YAML parser needed."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, False
    fields = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fields, "".join(lines[i + 1:]), True
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return {}, text, False  # never closed


def frontmatter(fields: dict) -> str:
    out = ["---"]
    for k, v in fields.items():
        out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n"


def unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def json_list(raw: str) -> list[str] | None:
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return v
    return None


# --- notes and addenda sections

@dataclass
class Section:
    heading: str
    type: str | None = None
    aliases: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    line: int = 0
    problems: list[str] = field(default_factory=list)


def parse_sections(text: str) -> list[Section]:
    """`## <slug>` sections, each with optional `type:` and `aliases:` lines and its list items."""
    _, body, _ = split_frontmatter(text)
    offset = len(text.splitlines()) - len(body.splitlines())
    sections: list[Section] = []
    cur: Section | None = None
    for i, line in enumerate(body.splitlines(), start=offset + 1):
        if line.startswith("## "):
            cur = Section(heading=line[3:].strip(), line=i)
            sections.append(cur)
            if not is_slug(cur.heading):
                cur.problems.append(f"heading {cur.heading!r} is not a slug")
            continue
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.startswith("type:") and cur.type is None and not cur.facts:
            cur.type = stripped[5:].strip()
        elif stripped.startswith("aliases:") and not cur.facts:
            parsed = json_list(stripped[8:].strip())
            if parsed is None:
                cur.problems.append("aliases line is not a JSON array of strings")
            else:
                cur.aliases = parsed
        elif is_list_item(line):
            cur.facts.append(line.rstrip())
    return sections


# --- the aliases table

@dataclass
class AliasRow:
    slug: str
    type: str
    names: list[str]
    proposed: list[str]
    description: str
    line: int = 0


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # names hold a JSON array whose strings could contain "|": split outside brackets and quotes
    cells, cur, depth, quote = [], "", 0, False
    for ch in s:
        if ch == '"' and depth:
            quote = not quote
        if not quote:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            elif ch == "|" and depth == 0:
                cells.append(cur.strip())
                cur = ""
                continue
        cur += ch
    cells.append(cur.strip())
    return cells


def parse_aliases(text: str) -> tuple[list[AliasRow], list[str]]:
    """(rows, problems) from _aliases.md's table."""
    rows, problems = [], []
    _, body, _ = split_frontmatter(text)
    offset = len(text.splitlines()) - len(body.splitlines())
    for i, line in enumerate(body.splitlines(), start=offset + 1):
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if [c.lower() for c in cells[:2]] == ["slug", "type"] or all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != 5:
            problems.append(f"line {i}: expected 5 columns, found {len(cells)}")
            continue
        slug, typ, names_raw, proposed_raw, desc = cells
        names = json_list(names_raw)
        if names is None:
            problems.append(f"line {i}: names is not a JSON array")
            names = []
        proposed = [p.strip() for p in proposed_raw.split(",") if p.strip()]
        if not is_slug(slug):
            problems.append(f"line {i}: slug {slug!r} breaks the slug rule")
        rows.append(AliasRow(slug, typ, names, proposed, desc, i))
    return rows, problems


def render_aliases(rows: list[AliasRow]) -> str:
    out = [
        frontmatter({"name": "_aliases", "description": "maps every notes heading to one canonical slug"}),
        "| slug | type | names | proposed | description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(f"| {r.slug} | {r.type} | {json.dumps(r.names, ensure_ascii=False)} | "
                   f"{', '.join(r.proposed)} | {r.description} |")
    return "\n".join(out) + "\n"


def resolve_heading(rows: list[AliasRow], unit: str, heading: str) -> list[str]:
    """The slugs of every row whose proposed list names this unit's heading (exactly one is correct)."""
    hits = []
    for r in rows:
        if heading in r.proposed or f"{unit}#{heading}" in r.proposed:
            hits.append(r.slug)
    return hits


def links_in(text: str) -> list[str]:
    return [t.strip() for t in WIKILINK.findall(CODE_SPAN.sub("", text))]


def current_picture(text: str) -> tuple[int, int] | None:
    """(start, end) character offsets of the text between the markers, or None."""
    a = text.find(MARK_START)
    b = text.find(MARK_END)
    if a == -1 or b == -1 or b < a:
        return None
    return a + len(MARK_START), b


def outside_markers(text: str) -> str:
    span = current_picture(text)
    if not span:
        return text
    return text[: span[0]] + text[span[1]:]
