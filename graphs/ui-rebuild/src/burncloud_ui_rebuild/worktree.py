from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorktreeError(RuntimeError):
    """Backward-compatible name for branch lifecycle failures.

    Harness v1 no longer creates Git worktrees. The class name is retained so
    existing imports do not break during the migration to single-checkout mode.
    """


AGENT_BRANCH_PREFIX = "agent/ui-rebuild/"
COMPLETED_BRANCH_CONFIG = "burncloud.harness.completedBranch"


def _run_git(root: Path, *args: str, timeout: int = 60, check: bool = True) -> tuple[int, str]:
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
    if check and completed.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed ({completed.returncode}): {output}")
    return completed.returncode, output


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    return _run_git(root, *args, timeout=timeout, check=True)[1]


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


def _completed_branch(root: Path) -> str:
    code, output = _run_git(root, "config", "--local", "--get", COMPLETED_BRANCH_CONFIG, check=False)
    return output.strip() if code == 0 else ""


def mark_agent_branch_completed(repo_root: str | Path, branch: str | None = None) -> dict[str, str]:
    """Mark the current Agent branch complete without switching branches.

    The next new Harness run may then return to main and create a fresh branch.
    Failed/blocked runs never call this function, so retries naturally stay on
    the same branch and keep the same Cargo target directory.
    """
    root = Path(repo_root).resolve()
    actual = current_branch(root)
    selected = _validate_branch_name(branch or actual)
    if actual != selected:
        raise WorktreeError(f"Cannot mark non-current Agent branch complete: current={actual!r}, requested={selected!r}")
    _git(root, "config", "--local", COMPLETED_BRANCH_CONFIG, selected)
    return {"status": "completed", "agent_branch": selected, "source_repo_root": str(root)}


def _clear_completed_branch(root: Path) -> None:
    _run_git(root, "config", "--local", "--unset", COMPLETED_BRANCH_CONFIG, check=False)


def agent_branch_is_completed(repo_root: str | Path, branch: str | None = None) -> bool:
    root = Path(repo_root).resolve()
    selected = branch or current_branch(root)
    return bool(selected) and _completed_branch(root) == selected


def _create_agent_branch(root: Path, *, base_branch: str) -> dict[str, Any]:
    if current_branch(root) != base_branch:
        raise WorktreeError(f"Expected checkout on {base_branch!r} before creating a new Agent branch.")
    status = porcelain_status(root)
    if status:
        raise WorktreeError(
            f"{base_branch!r} must be clean before creating a new Agent branch. Current git status:\n{status}"
        )

    base_commit = head_commit(root)
    agent_branch = _validate_branch_name(f"{AGENT_BRANCH_PREFIX}{_run_slug()}")
    _git(root, "switch", "-c", agent_branch, base_commit, timeout=120)
    _clear_completed_branch(root)

    actual = current_branch(root)
    if actual != agent_branch:
        raise WorktreeError(f"Created branch is unexpected: {actual!r}; expected {agent_branch!r}.")
    if porcelain_status(root):
        raise WorktreeError("Fresh Agent branch is unexpectedly dirty.")

    return {
        "base_repo_root": str(root),
        "base_branch": base_branch,
        "base_commit": base_commit,
        "agent_branch": agent_branch,
        "source_repo_root": str(root),
        "branch_reused": False,
        # Compatibility fields: no worktree is created. Both roots are the one checkout.
        "worktree_root": str(root),
        "worktree_reused": False,
    }


def find_reusable_agent_worktree(base_repo_root: str | Path) -> dict[str, str] | None:
    """Compatibility helper: return the current in-place Agent branch, if active.

    No `git worktree list` scan occurs. This is deliberate: an old branch is
    resumed only when the one BurnCloud checkout is already on that branch.
    """
    root = Path(base_repo_root).resolve()
    if not root.exists():
        return None
    branch = current_branch(root)
    if not branch.startswith(AGENT_BRANCH_PREFIX):
        return None
    try:
        _validate_branch_name(branch)
    except WorktreeError:
        return None
    if agent_branch_is_completed(root, branch):
        return None
    return {
        "agent_branch": branch,
        "worktree_root": str(root),
        "source_repo_root": str(root),
    }


def prepare_agent_worktree(
    base_repo_root: str | Path,
    *,
    base_branch: str = "main",
    worktree_parent: str | Path | None = None,
    start_new_task: bool = False,
) -> dict[str, Any]:
    """Prepare one in-place Agent branch while preserving Cargo target cache.

    Lifecycle:
    - main + clean -> create a fresh agent/ui-rebuild/* branch.
    - active Agent branch -> keep using it, including dirty in-progress changes.
    - completed Agent branch -> on the next run switch to main and create a new branch.
    - `start_new_task=True` behaves like completed, but refuses to abandon dirty work.
    - any unrelated branch -> fail closed.

    `worktree_parent` is accepted only for API compatibility and is ignored.
    No Git worktree is created.
    """
    del worktree_parent
    root = Path(base_repo_root).resolve()
    if not root.exists():
        raise WorktreeError(f"BurnCloud repository does not exist: {root}")

    branch_now = current_branch(root)
    status = porcelain_status(root)

    if branch_now.startswith(AGENT_BRANCH_PREFIX):
        _validate_branch_name(branch_now)
        completed = agent_branch_is_completed(root, branch_now)
        if not start_new_task and not completed:
            base_commit = _git(root, "merge-base", base_branch, branch_now).strip()
            return {
                "base_repo_root": str(root),
                "base_branch": base_branch,
                "base_commit": base_commit,
                "agent_branch": branch_now,
                "source_repo_root": str(root),
                "branch_reused": True,
                "worktree_root": str(root),
                "worktree_reused": True,
            }

        if status:
            reason = "explicit new task" if start_new_task else "completed task rollover"
            raise WorktreeError(
                f"Cannot perform {reason} while Agent branch {branch_now!r} is dirty. "
                "Retry/fix on this branch first, or explicitly clean/checkpoint the task before starting over. "
                f"Current git status:\n{status}"
            )
        _git(root, "switch", base_branch, timeout=120)
        return _create_agent_branch(root, base_branch=base_branch)

    if branch_now == base_branch:
        if status:
            raise WorktreeError(
                f"{base_branch!r} must be clean before starting a new Agent task. Current git status:\n{status}"
            )
        return _create_agent_branch(root, base_branch=base_branch)

    raise WorktreeError(
        f"BurnCloud checkout is on unrelated branch {branch_now!r}. "
        f"Use {base_branch!r} for a new task or an {AGENT_BRANCH_PREFIX}* branch to resume a task."
    )
