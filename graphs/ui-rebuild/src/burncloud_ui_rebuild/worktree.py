from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed ({completed.returncode}): {output}")
    return output


def current_branch(repo_root: str | Path) -> str:
    return _git(Path(repo_root).resolve(), "branch", "--show-current").strip()


def head_commit(repo_root: str | Path) -> str:
    return _git(Path(repo_root).resolve(), "rev-parse", "HEAD").strip()


def porcelain_status(repo_root: str | Path) -> str:
    return _git(Path(repo_root).resolve(), "status", "--porcelain").strip()


def _run_slug() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"{timestamp}-{suffix}"


def _validate_branch_name(branch: str) -> str:
    if not re.fullmatch(r"agent/ui-rebuild/[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", branch):
        raise WorktreeError(f"Refusing unexpected Agent branch name: {branch}")
    return branch


def prepare_agent_worktree(
    base_repo_root: str | Path,
    *,
    base_branch: str = "main",
    worktree_parent: str | Path | None = None,
) -> dict[str, str]:
    """Create a unique isolated branch + worktree pinned to the clean base checkout HEAD."""
    base = Path(base_repo_root).resolve()
    if not base.exists():
        raise WorktreeError(f"Base repository does not exist: {base}")

    branch_now = current_branch(base)
    if branch_now != base_branch:
        raise WorktreeError(
            f"Base checkout must be on {base_branch!r} before an Agent run; current branch is {branch_now!r}."
        )

    status = porcelain_status(base)
    if status:
        raise WorktreeError(
            "Base checkout must be clean before creating an Agent worktree. "
            f"Current git status:\n{status}"
        )

    base_commit = head_commit(base)
    slug = _run_slug()
    agent_branch = _validate_branch_name(f"agent/ui-rebuild/{slug}")
    parent = Path(worktree_parent).resolve() if worktree_parent else (base.parent / "burncloud-worktrees").resolve()
    worktree = parent / f"ui-rebuild-{slug}"
    parent.mkdir(parents=True, exist_ok=True)

    if worktree.exists():
        raise WorktreeError(f"Generated worktree path already exists: {worktree}")

    _git(base, "worktree", "add", "-b", agent_branch, str(worktree), base_commit, timeout=120)

    actual_branch = current_branch(worktree)
    if actual_branch != agent_branch:
        raise WorktreeError(
            f"Created worktree is on unexpected branch {actual_branch!r}; expected {agent_branch!r}."
        )
    if porcelain_status(worktree):
        raise WorktreeError("Fresh Agent worktree is unexpectedly dirty.")

    return {
        "base_repo_root": str(base),
        "base_branch": base_branch,
        "base_commit": base_commit,
        "agent_branch": agent_branch,
        "worktree_root": str(worktree),
        "source_repo_root": str(worktree),
    }
