from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from burncloud_ui_rebuild.worktree import (
    WorktreeError,
    agent_branch_is_completed,
    current_branch,
    head_commit,
    mark_agent_branch_completed,
    porcelain_status,
    prepare_agent_worktree,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "burncloud"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "agent-test@example.com")
    _git(repo, "config", "user.name", "Agent Test")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "baseline")
    return repo


def test_prepare_agent_branch_uses_same_checkout(tmp_path: Path):
    repo = _repo(tmp_path)
    baseline = head_commit(repo)

    result = prepare_agent_worktree(repo)

    assert current_branch(repo) == result["agent_branch"]
    assert result["agent_branch"].startswith("agent/ui-rebuild/")
    assert result["agent_branch"] != "main"
    assert result["branch_reused"] is False
    assert Path(result["source_repo_root"]) == repo.resolve()
    assert Path(result["worktree_root"]) == repo.resolve()  # compatibility alias only
    assert head_commit(repo) == baseline
    assert result["base_commit"] == baseline
    assert porcelain_status(repo) == ""


def test_failed_or_in_progress_run_reuses_current_agent_branch_and_dirty_state(tmp_path: Path):
    repo = _repo(tmp_path)
    first = prepare_agent_worktree(repo)
    branch = first["agent_branch"]

    (repo / "README.md").write_text("in progress\n", encoding="utf-8")
    assert porcelain_status(repo)

    second = prepare_agent_worktree(repo)

    assert second["branch_reused"] is True
    assert second["agent_branch"] == branch
    assert Path(second["source_repo_root"]) == repo.resolve()
    assert porcelain_status(repo)


def test_completed_task_rolls_to_new_branch_from_main_on_next_run(tmp_path: Path):
    repo = _repo(tmp_path)
    first = prepare_agent_worktree(repo)
    first_branch = first["agent_branch"]
    assert agent_branch_is_completed(repo) is False

    mark_agent_branch_completed(repo)
    assert agent_branch_is_completed(repo, first_branch) is True

    second = prepare_agent_worktree(repo)

    assert second["branch_reused"] is False
    assert second["agent_branch"] != first_branch
    assert current_branch(repo) == second["agent_branch"]
    assert second["base_branch"] == "main"


def test_explicit_new_task_refuses_to_abandon_dirty_agent_branch(tmp_path: Path):
    repo = _repo(tmp_path)
    prepare_agent_worktree(repo)
    (repo / "README.md").write_text("unfinished\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="dirty"):
        prepare_agent_worktree(repo, start_new_task=True)


def test_prepare_agent_branch_refuses_dirty_main(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="must be clean"):
        prepare_agent_worktree(repo)


def test_prepare_agent_branch_refuses_unrelated_branch(tmp_path: Path):
    repo = _repo(tmp_path)
    _git(repo, "switch", "-c", "feature/local")

    with pytest.raises(WorktreeError, match="unrelated branch"):
        prepare_agent_worktree(repo)
