"""wiki.toml: models, caps, turn limits, timeouts, schema and steering paths."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import WikiError

ROLES = (
    "extract", "check", "resolve", "link", "compile", "index",
    "ask", "dream_analyst", "dream_apply", "grader",
)

TEMPLATE = """\
# wiki workspace settings. Only the owner raises caps.total_usd.

schema = "{schema}"          # seeded into repo/wiki/_schema.md by wiki init
steering = "{steering}"      # filled into every role prompt's {{steering}} block
escalations = {escalations}  # whether Index writes escalations.md
tier = "{tier}"              # selects the build cap below: quickstart, core or other

[models]                     # a Claude Code model alias per role
extract = "haiku"
check = "haiku"
resolve = "haiku"
link = "haiku"
compile = "haiku"
index = "haiku"
ask = "haiku"
dream_analyst = "haiku"
dream_apply = "haiku"
grader = "sonnet"

[caps]                       # US dollars
total_usd = 50.0
build_quickstart_usd = 6.0
build_core_usd = 25.0
build_other_usd = 6.0
question_usd = 0.50
dream_usd = 3.0
eval_usd = 3.0

[runner]
min_claude_version = "2.1.302"
concurrency = 4
batch_chars = 120000
resolve_limit = 10
compile_group = 10
dream_group = 10
dream_min_transcripts = 5
reserve = 0.10               # share of each cap held back when splitting it across runs
# api_key_settings = "/path/to/settings.json"   # a --settings file with an apiKeyHelper, for unattended runs

[limits.extract]
turns = 150
timeout_s = 1800
[limits.check]
turns = 150
timeout_s = 1800
[limits.resolve]
turns = 150
timeout_s = 1800
[limits.dream_apply]
turns = 150
timeout_s = 1800
[limits.link]
turns = 60
timeout_s = 900
[limits.index]
turns = 60
timeout_s = 900
[limits.compile]
turns = 40
timeout_s = 600
[limits.ask]
turns = 40
timeout_s = 300
[limits.dream_analyst]
turns = 40
timeout_s = 300
[limits.grader]
turns = 5
timeout_s = 120
"""


def render(schema: str, steering: str, tier: str = "other", escalations: bool = True) -> str:
    return TEMPLATE.format(
        schema=schema, steering=steering, tier=tier, escalations="true" if escalations else "false"
    )


@dataclass
class Config:
    path: Path
    data: dict

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            data = tomllib.loads(Path(path).read_text())
        except FileNotFoundError:
            raise WikiError(f"{path} not found: is this a wiki workspace? (wiki init <dir>)") from None
        except tomllib.TOMLDecodeError as why:
            raise WikiError(f"{path}: {why}") from None
        return cls(Path(path), data)

    def model(self, role: str) -> str:
        return self.data.get("models", {}).get(role, "haiku")

    def cap(self, name: str) -> float:
        return float(self.data["caps"][name])

    @property
    def total_usd(self) -> float:
        return self.cap("total_usd")

    def build_cap(self) -> float:
        tier = self.data.get("tier", "other")
        caps = self.data["caps"]
        return float(caps.get(f"build_{tier}_usd", caps.get("build_other_usd", 6.0)))

    def runner(self, key: str, default=None):
        return self.data.get("runner", {}).get(key, default)

    def turns(self, role: str) -> int:
        return int(self.data.get("limits", {}).get(role, {}).get("turns", 40))

    def timeout(self, role: str) -> float:
        return float(self.data.get("limits", {}).get(role, {}).get("timeout_s", 300))

    def resolve_path(self, key: str) -> Path | None:
        value = self.data.get(key)
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else (self.path.parent / p)

    def steering_text(self) -> str:
        p = self.resolve_path("steering")
        return p.read_text() if p and p.exists() else ""

    @property
    def escalations(self) -> bool:
        return bool(self.data.get("escalations", False))

    def set_tier(self, tier: str) -> None:
        """Rewrite the tier line in place (the one setting the tool itself changes)."""
        text = self.path.read_text()
        lines = [
            (f'tier = "{tier}"' + line[line.index("#") - 1:] if "#" in line else f'tier = "{tier}"')
            if line.startswith("tier =") else line
            for line in text.splitlines()
        ]
        self.path.write_text("\n".join(lines) + "\n")
        self.data["tier"] = tier
