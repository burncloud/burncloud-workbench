from __future__ import annotations

import subprocess
from pathlib import Path

from burncloud_ui_rebuild.coding_tools import (
    build_coding_tools,
    create_page_checkpoint,
    head_commit,
    normalize_repo_path,
    restore_page_checkpoint,
)
from burncloud_ui_rebuild.engineering_nodes import (
    apply_budget_guard,
    budget_reason,
    plan_guard_node,
    scope_guard_node,
)
from burncloud_ui_rebuild.policy import DEFAULT_POLICY


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


def _agent_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "crates/client/src").mkdir(parents=True)
    (repo / "crates/server/src").mkdir(parents=True)
    (repo / "crates/client/src/app.rs").write_text("fn client() {}\n", encoding="utf-8")
    (repo / "crates/server/src/api.rs").write_text("fn server() {}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "switch", "-c", "agent/ui-rebuild/v1-test")
    return repo


def test_normalization_never_hides_parent_traversal():
    assert normalize_repo_path("./crates/client/src/app.rs") == "crates/client/src/app.rs"
    assert normalize_repo_path("../secret.txt") == "../secret.txt"


def test_plan_guard_rejects_traversal_backend_and_unlisted_step():
    result = plan_guard_node({
        "implementation_plan": {
            "status": "COMPLETE",
            "allowed_files": [
                "../secret.txt",
                "crates/server/src/api.rs",
                "crates/client/src/app.rs",
            ],
            "steps": [
                {"file": "crates/client/src/not_allowed.rs", "intent": "unexpected"},
            ],
        },
        "page_context": {},
    })
    codes = {item["code"] for item in result["plan_findings"]}
    assert "PLAN_UNSAFE_PATH" in codes
    assert "PLAN_OUTSIDE_UI_SCOPE" in codes
    assert "PLAN_STEP_NOT_ALLOWLISTED" in codes
    assert result["current_page_status"] == "plan_rejected"


def test_plan_guard_accepts_small_client_only_plan():
    result = plan_guard_node({
        "implementation_plan": {
            "status": "COMPLETE",
            "allowed_files": ["./crates/client/src/app.rs"],
            "steps": [{"file": "crates/client/src/app.rs", "intent": "add route"}],
        },
        "page_context": {},
    })
    assert result["plan_findings"] == []
    assert result["implementation_plan"]["allowed_files"] == ["crates/client/src/app.rs"]
    assert result["current_page_status"] == "plan_approved"


def test_builder_tool_refuses_file_outside_approved_plan(tmp_path: Path):
    repo = _agent_repo(tmp_path)
    workbench = tmp_path / "workbench"
    workbench.mkdir()
    tools = {
        item.name: item
        for item in build_coding_tools(
            source_root=repo,
            workbench_root=workbench,
            allow_write=True,
            expected_branch="agent/ui-rebuild/v1-test",
            allowed_write_files=["crates/client/src/app.rs"],
        )
    }

    ok = tools["replace_source_text"].invoke({
        "path": "crates/client/src/app.rs",
        "old": "fn client() {}",
        "new": "fn client() { println!(\"ok\"); }",
        "expected_occurrences": 1,
    })
    refused = tools["create_source_file"].invoke({
        "path": "crates/client/src/extra.rs",
        "content": "// extra\n",
    })
    assert ok.startswith("UPDATED")
    assert "PLAN_SCOPE_REFUSED" in refused
    assert not (repo / "crates/client/src/extra.rs").exists()


def test_scope_guard_rejects_unplanned_and_non_client_diff(tmp_path: Path):
    repo = _agent_repo(tmp_path)
    (repo / "crates/client/src/app.rs").write_text("fn client() { }\n", encoding="utf-8")
    (repo / "crates/server/src/api.rs").write_text("fn server() { }\n", encoding="utf-8")

    result = scope_guard_node({
        "execution_mode": "write",
        "source_repo_root": str(repo),
        "implementation_plan": {"allowed_files": ["crates/client/src/app.rs"]},
        "verification_findings": [],
    })
    codes = {item["code"] for item in result["verification_findings"]}
    assert "SCOPE_GUARD_UNPLANNED_FILES" in codes
    assert "SCOPE_GUARD_PROTECTED_DOMAIN" in codes
    assert result["current_page_status"] == "scope_failed"


def test_budget_guard_escalates_on_page_token_budget():
    state = {
        "budget_usage": {
            "run_started_at": 100.0,
            "page_started_at": 100.0,
            "total_tokens": DEFAULT_POLICY.max_page_tokens + 1,
            "page_total_tokens": DEFAULT_POLICY.max_page_tokens + 1,
            "page_agent_invocations": 1,
        },
        "verification_findings": [],
    }
    assert "token budget exceeded" in budget_reason(state, now=101.0)
    update = apply_budget_guard(state, {})
    assert update["current_page_status"] == "budget_exhausted"
    assert update["verification_findings"][0]["code"] == "HARNESS_BUDGET_EXHAUSTED"


def test_checkpoint_recovery_only_accepts_known_page_checkpoint(tmp_path: Path):
    repo = _agent_repo(tmp_path)
    (repo / "crates/client/src/app.rs").write_text("// page one\n", encoding="utf-8")
    first = create_page_checkpoint(repo, "buyer-overview")
    first_commit = str(first["commit"])

    (repo / "crates/client/src/app.rs").write_text("// page two\n", encoding="utf-8")
    second = create_page_checkpoint(repo, "buyer-playground")
    assert head_commit(repo) == second["commit"]

    restored = restore_page_checkpoint(repo, first_commit)
    assert restored["status"] == "restored"
    assert restored["page_id"] == "buyer-overview"
    assert head_commit(repo) == first_commit
