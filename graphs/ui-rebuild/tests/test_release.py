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


def test_release_preflight_checks_origin_and_gh_auth_before_model_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(release.shutil, "which", lambda name: "/usr/bin/gh")
    calls: list[list[str]] = []

    def fake_run(root: Path, argv: list[str], *, timeout: int = 120) -> str:
        calls.append(argv)
        if argv == ["git", "remote", "get-url", "origin"]:
            return "https://github.com/burncloud/burncloud.git"
        if argv == ["gh", "auth", "status"]:
            return "authenticated"
        raise AssertionError(argv)

    monkeypatch.setattr(release, "_run", fake_run)
    result = release.release_preflight(_base_state(tmp_path))

    assert result["release_preflight"]["status"] == "ready"
    assert result["release_preflight"]["draft_pr"] is True
    assert result["release_preflight"]["auto_merge"] is False
    assert ["gh", "auth", "status"] in calls


def test_release_preflight_fails_early_without_gh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    with pytest.raises(release.ReleaseError, match="GitHub CLI"):
        release.release_preflight(_base_state(tmp_path))


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


def test_merged_matching_pr_is_never_duplicated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
                "number": 56,
                "url": "https://github.com/burncloud/burncloud/pull/56",
                "title": "merged",
                "state": "MERGED",
                "isDraft": False,
            }])
        raise AssertionError(argv)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "mark_agent_branch_completed", lambda root, branch: {"status": "completed"})
    state = _base_state(tmp_path)
    state["human_decision"] = False
    state["branch_task_status"] = "completed_unintegrated"

    result = release.publish_pull_request(state)

    assert result["release_status"] == "pull_request_merged"
    assert result["branch_task_status"] == "merged_remote"
    assert not any(argv[:3] == ["gh", "pr", "create"] for argv in calls)


def test_closed_unmerged_pr_fails_closed_instead_of_creating_duplicate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _patch_repo(monkeypatch)

    def fake_run(root: Path, argv: list[str], *, timeout: int = 120) -> str:
        if argv[:3] == ["git", "rev-list", "--count"]:
            return "1"
        if argv[:3] == ["git", "diff", "--stat"]:
            return "diff"
        if argv[:2] == ["git", "push"]:
            return "pushed"
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps([{
                "number": 57,
                "url": "https://github.com/burncloud/burncloud/pull/57",
                "title": "closed",
                "state": "CLOSED",
                "isDraft": True,
            }])
        raise AssertionError(argv)

    monkeypatch.setattr(release, "_run", fake_run)
    state = _base_state(tmp_path)
    state["human_decision"] = False
    state["branch_task_status"] = "completed_unintegrated"

    with pytest.raises(release.ReleaseError, match="closed without merge"):
        release.publish_pull_request(state)


def test_release_refuses_dirty_completed_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(release, "current_branch", lambda root: BRANCH)
    monkeypatch.setattr(release, "porcelain_status", lambda root: " M crates/client/src/app.rs")

    with pytest.raises(release.ReleaseError, match="clean Agent branch"):
        release.publish_pull_request(_base_state(tmp_path))
