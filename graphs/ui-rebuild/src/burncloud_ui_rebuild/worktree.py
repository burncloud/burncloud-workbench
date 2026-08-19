from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorktreeError(RuntimeError):
    pass


AGENT_BRANCH_PREFIX = "agent/ui-rebuild/"


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
    if not re.fullmatch(r"agent/ui-rebuild/(?:[0-9]{8}-[0-9]{6}-[0-9a-f]{8}|current)", branch):
        raise WorktreeError(f"Refusing unexpected Agent branch name: {branch}")
    return branch


def _listed_worktrees(base: Path) -> list[dict[str, str]]:
    """Return Git worktree records from `git worktree list --porcelain`."""
    output = _git(base, "worktree", "list", "--porcelain")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if current:
            records.append(current)
            current = {}

    for line in output.splitlines():
        if not line.strip():
            flush()
            continue
        if line.startswith("worktree "):
            current["worktree_root"] = line[len("worktree ") :].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :].strip()
        elif line.startswith("branch refs/heads/"):
            current["agent_branch"] = line[len("branch refs/heads/") :].strip()
    flush()
    return records


def find_reusable_agent_worktree(base_repo_root: str | Path) -> dict[str, str] | None:
    """Find the newest existing UI rebuild worktree so later runs continue the same work."""
    base = Path(base_repo_root).resolve()
    candidates: list[dict[str, str]] = []
    for record in _listed_worktrees(base):
        branch = record.get("agent_branch", "")
        root_text = record.get("worktree_root", "")
        if not branch.startswith(AGENT_BRANCH_PREFIX) or not root_text:
            continue
        try:
            _validate_branch_name(branch)
        except WorktreeError:
            continue
        root = Path(root_text).resolve()
        if not root.exists():
            continue
        if current_branch(root) != branch:
            continue
        candidates.append({
            "agent_branch": branch,
            "worktree_root": str(root),
            "source_repo_root": str(root),
        })

    if not candidates:
        return None

    # Timestamped branch names sort chronologically, so the newest prior run wins.
    candidates.sort(key=lambda item: item["agent_branch"], reverse=True)
    return candidates[0]


def prepare_agent_worktree(
    base_repo_root: str | Path,
    *,
    base_branch: str = "main",
    worktree_parent: str | Path | None = None,
) -> dict[str, Any]:
    """Reuse the newest UI rebuild worktree, or create one when none exists yet."""
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
            "Base checkout must be clean before continuing an Agent worktree. "
            f"Current git status:\n{status}"
        )

    reusable = find_reusable_agent_worktree(base)
    if reusable is not None:
        agent_branch = reusable["agent_branch"]
        worktree = Path(reusable["worktree_root"]).resolve()
        base_commit = _git(base, "merge-base", base_branch, agent_branch).strip()
        return {
            "base_repo_root": str(base),
            "base_branch": base_branch,
            "base_commit": base_commit,
            "agent_branch": agent_branch,
            "worktree_root": str(worktree),
            "source_repo_root": str(worktree),
            "worktree_reused": True,
        }

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
        "worktree_reused": False,
    }
