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
    """Mark the current Agent branch complete without switching branches."""
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


def _listed_worktrees(root: Path) -> list[dict[str, str]]:
    """Read legacy linked worktrees only for one-time migration detection."""
    output = _git(root, "worktree", "list", "--porcelain")
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
            current["root"] = line[len("worktree ") :].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :].strip()
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/") :].strip()
    flush()
    return records


def find_legacy_agent_worktree(base_repo_root: str | Path) -> dict[str, str] | None:
    """Return the newest linked legacy Agent worktree, excluding the primary checkout."""
    root = Path(base_repo_root).resolve()
    candidates: list[dict[str, str]] = []
    for record in _listed_worktrees(root):
        branch = record.get("branch", "")
        path_text = record.get("root", "")
        if not branch.startswith(AGENT_BRANCH_PREFIX) or not path_text:
            continue
        try:
            _validate_branch_name(branch)
        except WorktreeError:
            continue
        path = Path(path_text).resolve()
        if path == root or not path.exists():
            continue
        candidates.append({"agent_branch": branch, "legacy_worktree_root": str(path)})
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["agent_branch"], reverse=True)
    return candidates[0]


def migrate_legacy_agent_worktree(base_repo_root: str | Path) -> dict[str, Any]:
    """Move the newest old Agent worktree back into the primary checkout.

    This is an explicit one-time migration operation. Dirty tracked/untracked
    work is preserved with a temporary Git stash, the linked worktree is
    removed, the primary checkout switches to the same Agent branch, and the
    stash is restored there. Ignored build artifacts such as target/ are never
    stashed, which is intentional because the primary checkout keeps its own
    persistent Cargo cache.
    """
    root = Path(base_repo_root).resolve()
    legacy = find_legacy_agent_worktree(root)
    if legacy is None:
        return {"status": "no_legacy_agent_worktree", "source_repo_root": str(root)}

    if current_branch(root) != "main":
        raise WorktreeError("Legacy worktree migration requires the primary BurnCloud checkout to be on 'main'.")
    if porcelain_status(root):
        raise WorktreeError("Primary BurnCloud checkout must be clean before legacy worktree migration.")

    legacy_root = Path(legacy["legacy_worktree_root"]).resolve()
    branch = _validate_branch_name(legacy["agent_branch"])
    legacy_status = porcelain_status(legacy_root)
    stash_created = False

    if legacy_status:
        before = _git(legacy_root, "stash", "list", "--format=%gd", timeout=60).splitlines()
        _git(
            legacy_root,
            "stash",
            "push",
            "-u",
            "-m",
            f"burncloud-harness-legacy-migration:{branch}",
            timeout=120,
        )
        after = _git(legacy_root, "stash", "list", "--format=%gd", timeout=60).splitlines()
        stash_created = bool(after and after != before)
        if porcelain_status(legacy_root):
            raise WorktreeError("Legacy worktree is still dirty after migration stash; refusing to remove it.")

    _git(root, "worktree", "remove", str(legacy_root), timeout=120)
    _git(root, "switch", branch, timeout=120)
    _clear_completed_branch(root)

    if stash_created:
        code, output = _run_git(root, "stash", "pop", "stash@{0}", timeout=120, check=False)
        if code != 0:
            raise WorktreeError(
                "Legacy worktree branch was adopted into the primary checkout, but restoring its temporary stash "
                f"reported conflicts. Resolve them on {branch!r}; the stash is preserved by Git. Output:\n{output}"
            )

    return {
        "status": "migrated",
        "agent_branch": branch,
        "source_repo_root": str(root),
        "legacy_worktree_root": str(legacy_root),
        "restored_dirty_changes": stash_created,
    }


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
        "worktree_root": str(root),
        "worktree_reused": False,
    }


def find_reusable_agent_worktree(base_repo_root: str | Path) -> dict[str, str] | None:
    """Compatibility helper returning only the current in-place Agent branch."""
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

    No new Git worktree is ever created. The legacy function name is kept only
    so older modules and persisted Studio state remain compatible.
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
        legacy = find_legacy_agent_worktree(root)
        if legacy is not None:
            raise WorktreeError(
                "A legacy Agent worktree still owns the previous UI rebuild branch. "
                "To preserve retry continuity, run `burncloud-ui-rebuild migrate-legacy-worktree --confirm` once "
                "before starting the new single-checkout branch workflow. "
                f"Legacy branch={legacy['agent_branch']} path={legacy['legacy_worktree_root']}"
            )
        return _create_agent_branch(root, base_branch=base_branch)

    raise WorktreeError(
        f"BurnCloud checkout is on unrelated branch {branch_now!r}. "
        f"Use {base_branch!r} for a new task or an {AGENT_BRANCH_PREFIX}* branch to resume a task."
    )
