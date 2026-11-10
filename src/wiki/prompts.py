"""Role prompts: one file per role in prompts/, filled per corpus and written read-only for a run."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import tool_root

ESCALATIONS_RULES = """\
- escalations.md: a ranked list of every red flag in catalog.md's `## red-flags` sections, most serious first.
  It opens with `## Digest`: one short line per ranked item (number, title, entity). Then one entry per item:
  a `### <rank>. <title>` heading (the program splits a long page at these headings), a one-line why-it-matters,
  and the red flag's fact line with its source tag, copied exactly.
  Withdrawn red flags (tag with `retracted`) are left out of catalog.md and are not ranked.
"""


def fill(template: str, **values) -> str:
    """Replace only the named {placeholders}, so other braces survive."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def load(role_file: str) -> str:
    return (tool_root() / "prompts" / f"{role_file}.md").read_text()


def render(role_file: str, steering: str = "", escalations: bool = False, resolve_limit: int = 10) -> str:
    text = load(role_file)
    return fill(
        text,
        common=load("_common"),
        steering=steering.strip() or "(none)",
        resolve_limit=resolve_limit,
        escalations_owned=", escalations.md" if escalations else "",
        escalations_stem=", `escalations`" if escalations else "",
        escalations_rules=ESCALATIONS_RULES if escalations else "",
    )


_WRITE_LOCK = threading.Lock()


def write_prompt(run_dir: Path, role_file: str, text: str) -> Path:
    folder = Path(run_dir) / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / f"{role_file}.md"
    with _WRITE_LOCK:  # concurrent runs share one prompt file: write it once, atomically, read-only
        if path.exists() and path.read_text() == text:
            return path
        tmp = folder / f".{role_file}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(text)
        os.chmod(tmp, 0o400)
        os.replace(tmp, path)
    return path
