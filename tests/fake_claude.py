"""A fake `claude` for offline tests, chosen with WIKI_CLAUDE_BIN.

It reads its task, writes canned files into its working directory, and emits stream-json with a made-up cost.
It can misbehave on purpose: WIKI_FAKE_MISBEHAVE="<mode>[:<role>[:<times>]]" with mode one of
outside, markers, input, fail, noresult, overcap, maxturns, badtools, extras, hang, lifecycle, slugcite; <role> may be
<role>@<text> to pick only runs whose task contains <text>. WIKI_FAKE_STATE is a folder for
counters; WIKI_FAKE_COST the cost each run reports.

Fixture documents carry marker lines: ENTITY: slug | type | Name; Name, FACT: slug | text,
RELATION: a | rel | b, METRIC: name | value | basis | period, QUESTION: text, REDFLAG: text,
CONTRACT: section | title | text.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wiki.grammar import (  # noqa: E402
    AliasRow, MARK_END, MARK_START, parse_aliases, parse_sections, render_aliases,
)

ROLES = {"extract": "Extract:", "check": "Check:", "resolve": "Resolve:", "link": "Link:", "compile": "Compile:",
         "index": "Index:", "ask": "Ask:", "dream_analyst": "Dream analyst:", "dream_apply": "Dream apply:",
         "grade": "Grade:"}


def emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def parse_args(argv):
    a = {"add_dirs": [], "tools": "", "task": "", "budget": 1.0, "model": "haiku", "prompt": None}
    i = 0
    while i < len(argv):
        x = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if x == "-p":
            a["task"] = nxt
            i += 2
        elif x == "--add-dir":
            a["add_dirs"].append(nxt)
            i += 2
        elif x == "--tools":
            a["tools"] = nxt
            i += 2
        elif x == "--max-budget-usd":
            a["budget"] = float(nxt)
            i += 2
        elif x == "--model":
            a["model"] = nxt
            i += 2
        elif x == "--append-system-prompt-file":
            a["prompt"] = nxt
            i += 2
        elif x in ("--permission-mode", "--permission-prompts", "--max-turns", "--output-format", "--settings"):
            i += 2
        else:
            i += 1
    return a


def role_of(task: str) -> str:
    for role, prefix in ROLES.items():
        if task.startswith(prefix):
            return role
    return "other"


def misbehaviour(role: str, task: str = "") -> str | None:
    spec = os.environ.get("WIKI_FAKE_MISBEHAVE", "")
    if not spec:
        return None
    parts = spec.split(":")
    mode = parts[0]
    want_role = parts[1] if len(parts) > 1 and parts[1] else None
    times = int(parts[2]) if len(parts) > 2 and parts[2] else None
    needle = None
    if want_role and "@" in want_role:
        want_role, needle = want_role.split("@", 1)
    if want_role and want_role != role:
        return None
    if needle and needle not in task:
        return None
    if times is not None:
        state = Path(os.environ.get("WIKI_FAKE_STATE", "/tmp"))
        counter = state / f"count-{mode}-{role}-{needle or ''}"
        n = int(counter.read_text()) if counter.exists() else 0
        if n >= times:
            return None
        counter.write_text(str(n + 1))
    return mode


def as_of(text: str) -> str:
    first = text.split("\n", 1)[0]
    for key in ("as-of", "filed", "added"):
        m = re.search(key + r": (\d{4}-\d{2}-\d{2})", first)
        if m:
            return m.group(1)
    return "2024-01-01"


def tag(unit, date, **extra):
    s = f"[source: {unit} | as-of: {date}"
    for k in ("valid_from", "valid_to", "retracted", "section"):
        if extra.get(k):
            s += f" | {k}: {extra[k]}"
    return s + "]"


def fm(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n"


def markers(text):
    out = {"ENTITY": [], "FACT": [], "RELATION": [], "METRIC": [], "QUESTION": [], "REDFLAG": [], "CONTRACT": []}
    for line in text.splitlines():
        m = re.match(r"^(ENTITY|FACT|RELATION|METRIC|QUESTION|REDFLAG|CONTRACT): (.*)$", line.strip())
        if m:
            out[m.group(1)].append([p.strip() for p in m.group(2).split("|")])
    return out


def do_extract(cwd: Path, inputs: list[Path]):
    written = []
    for d in inputs:
        for f in sorted(Path(d).glob("*.txt")):
            unit = f.stem
            text = f.read_text()
            date = as_of(text)
            mk = markers(text)
            ents = mk["ENTITY"] or [[unit + "-document", "event", unit]]
            body = [fm(unit, f"facts filed from {unit}")]
            for ent in ents:
                slug, typ = ent[0], ent[1] if len(ent) > 1 else "other"
                names = [n.strip() for n in (ent[2] if len(ent) > 2 else slug).split(";") if n.strip()]
                body.append(f"## {slug}\ntype: {typ}\naliases: {json.dumps(names)}")
                facts = [x for x in mk["FACT"] if x[0] == slug] or [[slug, f"{slug} is named in {unit}."]]
                for fact in facts:
                    body.append(f"- {fact[1]} {tag(unit, date)}")
            if mk["REDFLAG"]:
                body.append("## red-flags")
                body += [f"- {r[0]} {tag(unit, date)}" for r in mk["REDFLAG"]]
            notes = cwd / "notes" / f"{unit}.md"
            notes.parent.mkdir(exist_ok=True)
            notes.write_text("\n".join(body) + "\n")
            written.append(notes)
            if mk["RELATION"]:
                p = cwd / "relations" / f"{unit}.md"
                p.parent.mkdir(exist_ok=True)
                p.write_text(fm(unit, f"relations in {unit}") + "".join(
                    f"- [[{r[0]}]] | {r[1]} | [[{r[2]}]] {tag(unit, date)}\n" for r in mk["RELATION"]))
            if mk["METRIC"]:
                p = cwd / "metrics" / f"{unit}.md"
                p.parent.mkdir(exist_ok=True)
                p.write_text(fm(unit, f"metrics in {unit}") + "".join(
                    f"- {' | '.join(r)} {tag(unit, date)}\n" for r in mk["METRIC"]))
            if mk["QUESTION"]:
                p = cwd / "open_questions" / f"{unit}.md"
                p.parent.mkdir(exist_ok=True)
                p.write_text(fm(unit, f"open questions from {unit}") + "".join(f"- {q[0]}\n" for q in mk["QUESTION"]))
            if mk["CONTRACT"]:
                p = cwd / "contracts" / f"{unit}.md"
                p.parent.mkdir(exist_ok=True)
                p.write_text(fm(unit, f"provisions of {unit}") + "".join(
                    f"### Section {c[0]} {c[1]}\n> {c[2]}\n{tag(unit, date, section=c[0])}\n\n" for c in mk["CONTRACT"]))
    return f"documents_filed: {len(written)}"


def first_heading(cwd: Path, unit: str) -> str | None:
    p = cwd / "notes" / f"{unit}.md"
    if not p.exists():
        return None
    secs = [s for s in parse_sections(p.read_text()) if s.heading != "red-flags"]
    return secs[0].heading if secs else None


def append_addendum(cwd: Path, unit: str, heading: str, line: str):
    p = cwd / "addenda" / f"{unit}.md"
    p.parent.mkdir(exist_ok=True)
    text = p.read_text() if p.exists() else fm(unit, f"addenda filed from {unit}")
    if f"## {heading}\n" in text:
        text = text.replace(f"## {heading}\n", f"## {heading}\n{line}\n", 1)
    else:
        text = text.rstrip("\n") + f"\n## {heading}\n{line}\n"
    p.write_text(text)


def do_check(cwd: Path, inputs: list[Path]):
    n = 0
    for d in inputs:
        for f in sorted(Path(d).glob("*.txt")):
            unit = f.stem
            h = first_heading(cwd, unit)
            if h:
                append_addendum(cwd, unit, h, f"- Checked against the unit. {tag(unit, as_of(f.read_text()))}")
                n += 1
    return f"verified: {n}"


def do_resolve(cwd: Path, docs: Path, limit=10):
    done = 0
    for p in sorted((cwd / "open_questions").glob("*.md")):
        unit = p.stem
        lines = p.read_text().splitlines()
        for i, line in enumerate(lines):
            if done >= limit:
                break
            if line.startswith("- ") and not line[2:].startswith(("RESOLVED:", "UNRESOLVABLE:")):
                h = first_heading(cwd, unit)
                doc = docs / f"{unit}.txt"
                date = as_of(doc.read_text()) if doc.exists() else "2024-01-01"
                if h:
                    append_addendum(cwd, unit, h, f"- Answer to: {line[2:]} {tag(unit, date)}")
                    lines[i] = f"- RESOLVED: {line[2:]} -> [[{h}]]"
                else:
                    lines[i] = f"- UNRESOLVABLE: {line[2:]}; request: more documents"
                done += 1
        p.write_text("\n".join(lines) + "\n")
    return f"resolved: {done}"


def do_link(cwd: Path, inputs: list[Path]):
    text = (Path(inputs[0]) / "headings.md").read_text()
    path = cwd / "_aliases.md"
    rows, _ = parse_aliases(path.read_text()) if path.exists() else ([], [])
    by_slug = {r.slug: r for r in rows}
    for block in re.findall(r"^## (\S+)#(\S+)\n(.*?)(?=^## |\Z|^# )", text, re.S | re.M):
        unit, heading, rest = block
        typ = re.search(r"^type: (.*)$", rest, re.M)
        al = re.search(r"^aliases: (.*)$", rest, re.M)
        names = json.loads(al.group(1)) if al else []
        row = by_slug.get(heading)
        if row is None:
            row = AliasRow(heading, (typ.group(1).strip() if typ else "") or "other", [], [], f"the {heading} entity")
            by_slug[heading] = row
            rows.append(row)
        if heading not in row.proposed:
            row.proposed.append(heading)
        for n in names:
            if n not in row.names:
                row.names.append(n)
    path.write_text(render_aliases(rows))
    return f"rows: {len(rows)}"


def do_compile(cwd: Path, task: str, bad_markers: bool):
    pages = re.findall(r"(entities/[a-z0-9-]+\.md)", task)
    for rel in pages:
        p = cwd / rel
        text = p.read_text()
        a, b = text.index(MARK_START) + len(MARK_START), text.index(MARK_END)
        facts = []
        for f, where in re.findall(r"^\| [a-z0-9-]+ \| ([a-z_]+/[a-z0-9-]+\.md) \| `## ([a-z0-9-]+)` \|", text[b:], re.M):
            body = (cwd / f).read_text().split(f"## {where}\n", 1)
            if len(body) == 2:
                facts += [ln for ln in body[1].splitlines() if ln.startswith("- ") and "[source:" in ln][:1]
        pic = "\n" + (facts[0] + "\n" if facts else "") + "Sections: the facts below, by unit.\n"
        text = text[:a] + pic + text[b:]
        if bad_markers:
            text += "\nAn edit outside the markers.\n"
        p.write_text(text)
    return f"pages: {len(pages)}"


def do_index(cwd: Path, inputs: list[Path], task: str):
    cat = (Path(inputs[0]) / "catalog.md").read_text()
    pages = re.findall(r"^## (entities/[a-z0-9-]+)\.md$", cat, re.M)
    units = re.findall(r"^- ([a-z0-9-]+): ", cat.split("# Units", 1)[1].split("# Red flags")[0], re.M)
    flags = [ln for ln in cat.split("# Red flags", 1)[1].splitlines() if ln.startswith("- ")]
    out = [fm("_index", "where to start, then every page and unit"), "## Where to start",
           "- Start with the entity pages below and [[README]].", "", "## Entities"]
    out += [f"- [[{Path(p).name}]] — an entity" for p in pages]
    out += ["", "## Units"] + [f"- {u}: notes/{u}.md" for u in units]
    (cwd / "_index.md").write_text("\n".join(out) + "\n")
    (cwd / "README.md").write_text(fm("README", "how to read this wiki") + "Start at [[_index]].\n")
    if "escalations.md" in task:
        (cwd / "escalations.md").write_text(fm("escalations", "ranked red flags") + "## Digest\n" + "\n".join(flags) + "\n")
    return f"index_entries: {len(pages) + len(units)}"


def do_ask(cwd: Path, task: str, add_dirs: list[Path]):
    q = task.split("Ask:", 1)[1].split("\n(Source documents", 1)[0].split("\n\n", 1)[0].strip()
    is_wiki = (cwd / "_index.md").exists()
    if "2025" in q:
        return "Not in the wiki" if is_wiki else "Not in the documents", ["Read"]
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", q)]
    if is_wiki:
        for p in sorted((cwd / "entities").glob("*.md")):
            for line in p.read_text().splitlines():
                if line.startswith("- ") and "[source:" in line and any(w in line.lower() for w in words):
                    return line[2:], ["Read", "Grep"]
        return "Not in the wiki; a document on this would answer it.", ["Read"]
    for f in sorted(cwd.glob("*.txt")):
        text = f.read_text()
        for line in text.splitlines():
            if line.startswith("FACT:") and any(w in line.lower() for w in words):
                return f"{line.split('|', 1)[1].strip()} {tag(f.stem, as_of(text))}", ["Grep", "Read"]
    return "Not in the documents.", ["Grep"]


def do_dream_analyst(add_dirs: list[Path]):
    docs = [d for d in add_dirs if list(Path(d).glob("*.txt"))]
    if not docs:
        return "- NONE"
    f = sorted(Path(docs[-1]).glob("*.txt"))[0]
    return f'- MISS: a fact readers missed | unit: {f.stem} | evidence: "see unit" {tag(f.stem, as_of(f.read_text()))}'


def do_dream_apply(cwd: Path, add_dirs: list[Path], break_lifecycle: bool):
    findings = [ln for d in add_dirs for p in Path(d).glob("findings-*.md") for ln in p.read_text().splitlines()
                if ln.startswith("- MISS")]
    n = 0
    for f in findings[:1]:
        unit = re.search(r"unit: ([a-z0-9-]+)", f).group(1)
        h = first_heading(cwd, unit)
        date = re.search(r"as-of: (\d{4}-\d{2}-\d{2})", f).group(1)
        append_addendum(cwd, unit, h, f"- Dream: a fact readers missed, confirmed in the unit. {tag(unit, date)}")
        notes = cwd / "notes" / f"{unit}.md"
        lines = notes.read_text().splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("- ") and ln.rstrip().endswith("]") and "retracted" not in ln:
                lines[i] = ln.rstrip()[:-1] + " | retracted: 2024-12-31]"
                if break_lifecycle:
                    lines[i] = lines[i].replace("- ", "- CHANGED ", 1)
                break
        notes.write_text("\n".join(lines) + "\n")
        n += 1
    return f"confirmed: {n}"


def do_grade(cwd: Path):
    crit = [ln for ln in (cwd / "criteria.md").read_text().splitlines() if ln.strip()]
    ans = (cwd / "answer.md").read_text().split("# Answer", 1)[1].lower()
    met = []
    for c in crit:
        if "refus" in c.lower() or "not in" in c.lower():
            met.append("not in the" in ans)
        else:
            met.append("[source:" in ans)
    return json.dumps({"met": met, "notes": ["fake" for _ in met]})


def main():
    argv = sys.argv[1:]
    if "--version" in argv or "-v" in argv:
        print(os.environ.get("WIKI_FAKE_VERSION", "2.1.400 (Claude Code)"))
        return 0
    a = parse_args(argv)
    cwd = Path.cwd()
    role = role_of(a["task"])
    mode = misbehaviour(role, a["task"])
    sid = str(uuid.uuid4())
    tools = [t for t in a["tools"].split(",") if t] + ["EndConversation"]
    if mode == "badtools":
        tools.append("Bash")
    init = {"type": "system", "subtype": "init", "session_id": sid, "tools": tools, "model": a["model"],
            "cwd": str(cwd), "permissionMode": "acceptEdits", "mcp_servers": [], "skills": [],
            "plugins": [{"name": "cc-plugin-privacy", "path": "builtin"}], "apiKeySource": "ANTHROPIC_API_KEY"}
    if mode == "extras":
        init["mcp_servers"] = [{"name": "stray", "status": "connected"}]
        init["plugins"].append({"name": "user-plugin", "path": "/somewhere"})
    emit(init)
    if mode in ("badtools", "extras"):
        time.sleep(5)  # the runner should stop us
    if mode == "hang":
        signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
        time.sleep(600)
    if mode == "noresult":
        return 1
    cost = float(os.environ.get("WIKI_FAKE_COST", "0.01"))
    if mode in ("fail", "overcap", "maxturns"):
        sub = {"fail": "error_during_execution", "overcap": "error_max_budget_usd", "maxturns": "error_max_turns"}[mode]
        emit({"type": "result", "subtype": sub, "is_error": True, "session_id": sid, "num_turns": 3,
              "total_cost_usd": a["budget"] * 1.2 if mode == "overcap" else cost, "errors": [sub],
              "usage": {"input_tokens": 10, "output_tokens": 5}, "permission_denials": []})
        return 1
    add_dirs = [Path(d) for d in a["add_dirs"]]
    uses = ["Read"]
    if role == "extract":
        text = do_extract(cwd, add_dirs)
    elif role == "check":
        text = do_check(cwd, add_dirs)
    elif role == "resolve":
        text = do_resolve(cwd, add_dirs[0])
    elif role == "link":
        text = do_link(cwd, add_dirs)
    elif role == "compile":
        text = do_compile(cwd, a["task"], mode == "markers")
    elif role == "index":
        text = do_index(cwd, add_dirs, a["task"])
    elif role == "ask":
        text, uses = do_ask(cwd, a["task"], add_dirs)
        if mode == "slugcite" and "not source units" not in a["task"]:
            slug = next((p.stem for p in sorted((cwd / "entities").glob("*.md"))), "acme")
            text = re.sub(r"\[source: [^ |\]]+", f"[source: {slug}", text) + " [source: _schema.md | as-of: n/a]"
    elif role == "dream_analyst":
        text = do_dream_analyst(add_dirs)
    elif role == "dream_apply":
        text = do_dream_apply(cwd, add_dirs, mode == "lifecycle")
    elif role == "grade":
        text = do_grade(cwd)
    else:
        text = "OK"
    if mode == "outside":
        target = cwd / "entities" / "intruder.md" if role != "index" else cwd / "notes" / "intruder.md"
        target.parent.mkdir(exist_ok=True)
        target.write_text(fm(target.stem, "written where this run does not own"))
    if mode == "input" and add_dirs:
        victim = next((p for p in sorted(add_dirs[0].rglob("*")) if p.is_file()), None)
        if victim:
            os.chmod(victim.parent, 0o700)
            os.chmod(victim, 0o600)
            victim.write_text(victim.read_text() + "\ntampered\n")
    emit({"type": "assistant", "session_id": sid, "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": u, "input": {"file_path": "_index.md"}} for u in uses]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "num_turns": 4,
          "result": text, "total_cost_usd": cost,
          "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 200},
          "modelUsage": {a["model"]: {"inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 1000,
                                      "cacheCreationInputTokens": 200, "costUSD": cost}},
          "permission_denials": []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
