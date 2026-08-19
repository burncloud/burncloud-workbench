from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from burncloud_ui_rebuild.worktree import (
    WorktreeError,
    current_branch,
    head_commit,
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


def test_prepare_agent_worktree_creates_isolated_branch(tmp_path: Path):
    repo = _repo(tmp_path)
    baseline = head_commit(repo)

    result = prepare_agent_worktree(repo)
    worktree = Path(result["worktree_root"])

    assert current_branch(repo) == "main"
    assert current_branch(worktree) == result["agent_branch"]
    assert result["agent_branch"].startswith("agent/ui-rebuild/")
    assert result["agent_branch"] != "main"
    assert result["worktree_reused"] is False
    assert head_commit(worktree) == baseline
    assert result["base_commit"] == baseline
    assert porcelain_status(repo) == ""
    assert porcelain_status(worktree) == ""
    assert worktree != repo


def test_prepare_agent_worktree_reuses_latest_existing_worktree(tmp_path: Path):
    repo = _repo(tmp_path)
    first = prepare_agent_worktree(repo)
    worktree = Path(first["worktree_root"])

    # Simulate a prior Agent run that left valid in-progress source changes.
    (worktree / "README.md").write_text("in progress\n", encoding="utf-8")
    assert porcelain_status(worktree)

    second = prepare_agent_worktree(repo)

    assert second["worktree_reused"] is True
    assert second["agent_branch"] == first["agent_branch"]
    assert second["worktree_root"] == first["worktree_root"]
    assert Path(second["worktree_root"]) == worktree
    assert porcelain_status(worktree)
    assert porcelain_status(repo) == ""


def test_prepare_agent_worktree_refuses_dirty_main(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="must be clean"):
        prepare_agent_worktree(repo)


def test_prepare_agent_worktree_refuses_non_main_base(tmp_path: Path):
    repo = _repo(tmp_path)
    _git(repo, "switch", "-c", "feature/local")

    with pytest.raises(WorktreeError, match="must be on 'main'"):
        prepare_agent_worktree(repo)
