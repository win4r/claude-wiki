"""wiki: build a Markdown knowledge wiki in a git repo with headless Claude Code runs."""

from pathlib import Path
import os

__version__ = "0.1.0"


def tool_root() -> Path:
    """The tool repo (prompts/, schemas/, examples/ live here)."""
    env = os.environ.get("WIKI_TOOL_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[2]


class WikiError(Exception):
    """An error reported to the user as a message, with an exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code
