from __future__ import annotations

import json
from pathlib import Path

import pytest

import burncloud_ui_rebuild.release as release


BRANCH = "agent/ui-rebuild/20260820-200000-1234abcd"


def _base_state(tmp_path: Path) -> dict:
    return {
        "execution_mode": "write",
        "human_decision": True,
        "base_branch": "main",
        "agent_branch": BRANCH,
        "source_repo_root": str(tmp_path),
        "final_findings": [],
        "notification_history": [],
        "budget_usage": {"total_tokens": 12345},
        "thread_id": "release-test",
    }


def _patch_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "current_branch", lambda root: BRANCH)
    monkeypatch.setattr(release, "porcelain_status", lambda root: "")
    monkeypatch.setattr(
        release,
        "checkpoint_history",
        lambda root: [{"commit": "abc123", "page_id": "buyer-overview"}],
    )
    monkeypatch.setattr(release.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)


def test_completed_run_pushes_and_creates_one_draft_pr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _patch_repo(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(root: Path, argv: list[str], *, timeout: int = 120) -> str:
        calls.append(argv)
        if argv[:3] == ["git", "rev-list", "--count"]:
            return "3"
        if argv[:3] == ["git", "diff", "--stat"]:
            return " crates/client/src/app.rs | 10 +++++-----"
        if argv[:2] == ["git", "push"]:
            return "pushed"
        if argv[:3] == ["gh", "pr", "list"]:
            return "[]"
        if argv[:3] == ["gh", "pr", "create"]:
            assert "--draft" in argv
            return "https://github.com/burncloud/burncloud/pull/321"
        if argv[:3] == ["gh", "pr", "view"]:
            return json.dumps({
                "number": 321,
                "url": "https://github.com/burncloud/burncloud/pull/321",
                "title": "feat(ui): rebuild buyer-overview",
                "state": "OPEN",
                "isDraft": True,
            })
        raise AssertionError(argv)

    monkeypatch.setattr(release, "_run", fake_run)
    completed: list[str] = []
    monkeypatch.setattr(
        release,
        "mark_agent_branch_completed",
        lambda root, branch: completed.append(branch) or {"status": "completed"},
    )

    result = release.publish_pull_request(_base_state(tmp_path))

    assert result["release_status"] == "pull_request_opened"
    assert result["branch_task_status"] == "awaiting_pr_merge"
    assert result["pull_request_number"] == 321
    assert result["pull_request_url"].endswith("/pull/321")
    assert completed == [BRANCH]
    assert sum(1 for argv in calls if argv[:3] == ["gh", "pr", "create"]) == 1
    assert any(argv[:2] == ["git", "push"] for argv in calls)


def test_existing_open_pr_is_reused_without_duplicate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _patch_repo(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(root: Path, argv: list[str], *, timeout: int = 120) -> str:
        calls.append(argv)
        if argv[:3] == ["git", "rev-list", "--count"]:
            return "1"
        if argv[:3] == ["git", "diff", "--stat"]:
            return "diff"
        if argv[:2] == ["git", "push"]:
            return "pushed"
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps([{
                "number": 55,
                "url": "https://github.com/burncloud/burncloud/pull/55",
                "title": "existing",
                "state": "OPEN",
                "isDraft": True,
            }])
        raise AssertionError(argv)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "mark_agent_branch_completed", lambda root, branch: {"status": "completed"})

    state = _base_state(tmp_path)
    state["human_decision"] = False
    state["branch_task_status"] = "completed_unintegrated"
    result = release.publish_pull_request(state)

    assert result["release_status"] == "pull_request_reused"
    assert result["pull_request_number"] == 55
    assert not any(argv[:3] == ["gh", "pr", "create"] for argv in calls)


def test_release_refuses_dirty_completed_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(release, "current_branch", lambda root: BRANCH)
    monkeypatch.setattr(release, "porcelain_status", lambda root: " M crates/client/src/app.rs")

    with pytest.raises(release.ReleaseError, match="clean Agent branch"):
        release.publish_pull_request(_base_state(tmp_path))
