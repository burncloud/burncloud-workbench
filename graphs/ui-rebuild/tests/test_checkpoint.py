from __future__ import annotations

import subprocess
from pathlib import Path

from burncloud_ui_rebuild.coding_tools import create_page_checkpoint, git_status, head_commit


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


def test_page_checkpoint_commits_only_on_agent_branch(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "switch", "-c", "agent/ui-rebuild/test-checkpoint")

    before = head_commit(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    result = create_page_checkpoint(repo, "buyer-overview")

    assert result["status"] == "committed"
    assert result["previous_commit"] == before
    assert result["commit"] != before
    assert git_status(repo) == ""
    assert _git(repo, "log", "-1", "--pretty=%s") == "agent(ui): checkpoint buyer-overview"
