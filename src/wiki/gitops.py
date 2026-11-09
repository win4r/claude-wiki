"""The few git operations the tool needs, through the git command line."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from . import WikiError

IDENTITY = ["-c", "user.name=wiki", "-c", "user.email=wiki@localhost", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def git(repo: Path, *args: str, check: bool = True, input: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *IDENTITY, "-C", str(repo), *args],
        capture_output=True, text=True, input=input,
    )
    if check and proc.returncode != 0:
        raise WikiError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def init_repo(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def is_clean(repo: Path) -> bool:
    # untracked files count whatever status.showUntrackedFiles says
    return git(repo, "status", "--porcelain", "--untracked-files=all").strip() == ""


def ignored(repo: Path, paths: list[str]) -> list[str]:
    """Those of these paths (relative to the repo, taken literally, present or not) that commit_paths could not add
    because a .gitignore rule or an excludes file ignores them. Each path is tried with the same add, dry-run, so
    the answer is git's own and needs none of its output read."""
    found = []
    for p in paths:
        proc = subprocess.run(["git", "-C", str(repo), "-c", "advice.addIgnoredFile=false", "--literal-pathspecs",
                               "add", "--dry-run", "--ignore-missing", "--", p], capture_output=True, text=True)
        if proc.returncode == 1:  # git add refuses an ignored path, and only that, with exit 1
            found.append(p)
        elif proc.returncode != 0:
            raise WikiError(f"git add --dry-run -- {p} failed: {proc.stderr.strip()}")
    return found


def commit_paths(repo: Path, paths: list[str], message: str) -> bool:
    """Commit only these paths (relative to the repo, taken literally, never as globs), whatever else is changed
    or staged; False when they hold no change. If the commit fails, the paths are unstaged again."""
    try:
        git(repo, "--literal-pathspecs", "add", "--", *paths)
        if not git(repo, "--literal-pathspecs", "diff", "--cached", "--name-only", "--", *paths).strip():
            return False
        git(repo, "--literal-pathspecs", "commit", "-q", "-m", message, "--", *paths)
    except WikiError:
        git(repo, "--literal-pathspecs", "reset", "-q", "--", *paths, check=False)
        raise
    return True


def commit_all(repo: Path, message: str) -> bool:
    """Stage everything and commit; False when there was nothing to commit."""
    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain").strip() == "":
        return False
    git(repo, "commit", "-q", "-m", message)
    return True


def changed_paths(worktree: Path) -> list[str]:
    """Paths changed, added or deleted in a worktree relative to its HEAD (untracked files included)."""
    out = git(worktree, "status", "--porcelain=v1", "-z", "-uall")
    paths = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if status[0] in "RC":  # a rename carries its old path as the next entry
            if i < len(entries):
                paths.append(entries[i])
            i += 1
    return sorted(set(paths))


# git's worktree bookkeeping (.git/worktrees) is not safe against concurrent add/remove/prune: serialize them
_WORKTREE_LOCK = threading.Lock()


def worktree_add(repo: Path, path: Path, ref: str = "HEAD", branch: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WORKTREE_LOCK:
        if branch:
            git(repo, "worktree", "add", "-q", "-b", branch, str(path), ref)
        else:
            git(repo, "worktree", "add", "-q", "--detach", str(path), ref)
    return path


def worktree_remove(repo: Path, path: Path) -> None:
    from .workspace import make_writable

    path = Path(path)
    if path.exists():
        make_writable(path)
    with _WORKTREE_LOCK:
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        git(repo, "worktree", "prune", check=False)


def show(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def ls_files(repo: Path, ref: str, prefix: str = "") -> list[str]:
    args = ["ls-tree", "-r", "--name-only", ref]
    if prefix:
        args += ["--", prefix]
    return [p for p in git(repo, *args).splitlines() if p]


def ref_exists(repo: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", ref],
                          capture_output=True).returncode == 0


def tags(repo: Path, pattern: str) -> list[str]:
    return [t for t in git(repo, "tag", "--list", pattern, "--sort=refname").splitlines() if t]


def branches(repo: Path, pattern: str) -> list[str]:
    out = git(repo, "branch", "--list", pattern, "--format=%(refname:short)")
    return [b for b in out.splitlines() if b]


def diff_stat(repo: Path, base: str, ref: str) -> str:
    return git(repo, "diff", "--stat", f"{base}..{ref}")


def diff_names(repo: Path, base: str, ref: str) -> list[str]:
    return [p for p in git(repo, "diff", "--name-only", f"{base}..{ref}").splitlines() if p]


def diff_text(repo: Path, base: str, ref: str) -> str:
    return git(repo, "diff", f"{base}..{ref}")
