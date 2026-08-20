from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from burncloud_ui_rebuild.worktree import (
    WorktreeError,
    agent_branch_is_completed,
    agent_branch_is_integrated,
    current_branch,
    head_commit,
    mark_agent_branch_completed,
    migrate_legacy_agent_worktree,
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
    (repo / ".gitignore").write_text("target/\n", encoding="utf-8")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-m", "baseline")
    return repo


def _commit_agent_change(repo: Path, text: str = "agent change\n") -> str:
    (repo / "agent-change.txt").write_text(text, encoding="utf-8")
    _git(repo, "add", "agent-change.txt")
    _git(repo, "commit", "-m", "agent change")
    return head_commit(repo)


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
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_single_checkout_keeps_target_cache_across_retry_and_integrated_rollover(tmp_path: Path):
    repo = _repo(tmp_path)
    cache = repo / "target/debug/incremental/cache-marker"
    cache.parent.mkdir(parents=True)
    cache.write_text("warm-cache\n", encoding="utf-8")

    first = prepare_agent_worktree(repo)
    first_branch = first["agent_branch"]
    _commit_agent_change(repo)
    assert cache.read_text(encoding="utf-8") == "warm-cache\n"

    # A failed/retried run stays on the same branch and same target directory.
    second = prepare_agent_worktree(repo)
    assert second["agent_branch"] == first_branch
    assert second["branch_reused"] is True
    assert cache.read_text(encoding="utf-8") == "warm-cache\n"

    # Human-approved completion alone is not enough to abandon an unmerged branch.
    mark_agent_branch_completed(repo)
    assert agent_branch_is_completed(repo, first_branch) is True
    assert agent_branch_is_integrated(repo, first_branch) is False
    still_same = prepare_agent_worktree(repo)
    assert still_same["agent_branch"] == first_branch
    assert still_same["branch_task_status"] == "completed_unintegrated"
    assert cache.read_text(encoding="utf-8") == "warm-cache\n"

    # Once main contains the task branch, the next run safely starts a new branch.
    _git(repo, "switch", "main")
    _git(repo, "merge", "--ff-only", first_branch)
    _git(repo, "switch", first_branch)
    assert agent_branch_is_integrated(repo, first_branch) is True

    third = prepare_agent_worktree(repo)
    assert third["agent_branch"] != first_branch
    assert current_branch(repo) == third["agent_branch"]
    assert cache.read_text(encoding="utf-8") == "warm-cache\n"
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


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


def test_completed_unintegrated_task_does_not_roll_to_main(tmp_path: Path):
    repo = _repo(tmp_path)
    first = prepare_agent_worktree(repo)
    first_branch = first["agent_branch"]
    _commit_agent_change(repo)
    mark_agent_branch_completed(repo)

    second = prepare_agent_worktree(repo)

    assert second["branch_reused"] is True
    assert second["agent_branch"] == first_branch
    assert second["branch_task_status"] == "completed_unintegrated"
    assert current_branch(repo) == first_branch


def test_completed_integrated_task_rolls_to_new_branch_from_main(tmp_path: Path):
    repo = _repo(tmp_path)
    first = prepare_agent_worktree(repo)
    first_branch = first["agent_branch"]
    _commit_agent_change(repo)
    mark_agent_branch_completed(repo)

    _git(repo, "switch", "main")
    _git(repo, "merge", "--ff-only", first_branch)
    _git(repo, "switch", first_branch)

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


def test_migrate_all_legacy_worktrees_preserves_current_and_archives_older_dirty_tasks(tmp_path: Path):
    repo = _repo(tmp_path)
    older = tmp_path / "legacy-older"
    active = tmp_path / "legacy-active"
    older_branch = "agent/ui-rebuild/20260820-120000-1111aaaa"
    active_branch = "agent/ui-rebuild/20260820-130000-2222bbbb"
    _git(repo, "worktree", "add", "-b", older_branch, str(older), "HEAD")
    _git(repo, "worktree", "add", "-b", active_branch, str(active), "HEAD")

    (older / "README.md").write_text("older dirty change\n", encoding="utf-8")
    (older / "older-untracked.txt").write_text("archive me\n", encoding="utf-8")
    (older / "target/debug").mkdir(parents=True)
    (older / "target/debug/old-cache").write_text("discard old cache\n", encoding="utf-8")

    (active / "README.md").write_text("active dirty change\n", encoding="utf-8")
    (active / "active-untracked.txt").write_text("restore me\n", encoding="utf-8")
    (active / "target/debug").mkdir(parents=True)
    (active / "target/debug/active-cache").write_text("discard old cache\n", encoding="utf-8")

    assert porcelain_status(older)
    assert porcelain_status(active)
    assert current_branch(repo) == "main"

    result = migrate_legacy_agent_worktree(repo)

    assert result["status"] == "migrated"
    assert result["agent_branch"] == active_branch
    assert result["restored_dirty_changes"] is True
    assert result["remaining_legacy_worktrees"] == 0
    assert current_branch(repo) == active_branch
    assert (repo / "README.md").read_text(encoding="utf-8") == "active dirty change\n"
    assert (repo / "active-untracked.txt").read_text(encoding="utf-8") == "restore me\n"
    assert not active.exists()
    assert not older.exists()
    assert len(result["archived_legacy_tasks"]) == 1
    archived = result["archived_legacy_tasks"][0]
    assert archived["agent_branch"] == older_branch
    assert archived["dirty_changes_preserved"] is True
    assert archived["stash_commit"]
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
