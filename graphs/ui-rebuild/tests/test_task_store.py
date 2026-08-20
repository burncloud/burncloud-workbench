from pathlib import Path

from burncloud_ui_rebuild.policy import DEFAULT_POLICY
from burncloud_ui_rebuild.task_store import (
    continuation_allowed,
    load_task_snapshot,
    restore_task_snapshot_node,
    save_task_snapshot,
    task_path,
)


PAGE = {
    "id": "buyer-overview",
    "role": "buyer",
    "page": "overview",
    "route": "/console/buyer",
    "contract_path": "docs/ui/page-contracts/buyer-overview.md",
    "phase": "golden",
}


def test_task_snapshot_compacts_and_restores_validation_stage(tmp_path: Path):
    branch = "agent/ui-rebuild/test-resume"
    state = {
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
        "branch_task_status": "active",
        "current_page": PAGE,
        "current_page_status": "budget_exhausted",
        "completed_pages": [],
        "page_context": {
            "page_id": "buyer-overview",
            "baseline_dirty_files": ["crates/client/src/app.rs"],
            "baseline_dirty_fingerprints": {"crates/client/src/app.rs": "abc"},
        },
        "scout_report": {"status": "COMPLETE", "summary": "x" * 10_000},
        "implementation_plan": {
            "status": "COMPLETE",
            "allowed_files": ["crates/client/src/app.rs"],
            "steps": [{"file": "crates/client/src/app.rs", "intent": "route"}],
        },
        "builder_report": {"status": "COMPLETE", "summary": "implemented"},
        "budget_usage": {"total_tokens": 5_000_001},
        "task_tokens_before_run": 2_000_000,
        "continuation_runs": 0,
        "verification_findings": [{"severity": "blocker", "code": "OLD", "message": "old failure"}],
        "review_findings": [],
    }

    saved = save_task_snapshot(state, safe_node="代码验证")
    assert saved["status"] == "saved"
    assert saved["task_total_tokens"] == 7_000_001

    raw = load_task_snapshot(branch, tmp_path)
    assert raw is not None
    assert raw["safe_node"] == "代码验证"
    assert "[compacted]" in raw["scout_report"]["summary"]

    restored = restore_task_snapshot_node({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
    })
    assert restored["resume_page_stage"] == "validate"
    assert restored["task_tokens_before_run"] == 7_000_001
    assert restored["continuation_runs"] == 1
    assert restored["budget_usage"] == {}
    assert restored["verification_findings"] == []
    assert restored["implementation_plan"]["allowed_files"] == ["crates/client/src/app.rs"]


def test_task_snapshot_restores_builder_when_plan_exists_but_was_not_implemented(tmp_path: Path):
    branch = "agent/ui-rebuild/test-build-resume"
    save_task_snapshot({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
        "current_page": PAGE,
        "current_page_status": "budget_exhausted",
        "page_context": {"page_id": "buyer-overview"},
        "scout_report": {"status": "COMPLETE"},
        "implementation_plan": {"status": "COMPLETE", "allowed_files": ["crates/client/src/app.rs"]},
        "builder_report": {},
        "budget_usage": {"total_tokens": 5_000_001},
    }, safe_node="修改计划")

    restored = restore_task_snapshot_node({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
    })
    assert restored["resume_page_stage"] == "build"


def test_task_snapshot_restores_planner_when_only_scout_is_complete(tmp_path: Path):
    branch = "agent/ui-rebuild/test-plan-resume"
    save_task_snapshot({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
        "current_page": PAGE,
        "current_page_status": "scouted",
        "page_context": {"page_id": "buyer-overview"},
        "scout_report": {"status": "COMPLETE", "relevant_files": ["crates/client/src/app.rs"]},
        "implementation_plan": {},
        "budget_usage": {"total_tokens": 1234},
    }, safe_node="代码侦察")

    restored = restore_task_snapshot_node({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
    })
    assert restored["resume_page_stage"] == "plan"
    assert restored["scout_report"]["status"] == "COMPLETE"


def test_pre_restore_startup_state_cannot_overwrite_existing_task(tmp_path: Path):
    branch = "agent/ui-rebuild/test-protected-snapshot"
    original = {
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
        "current_page": PAGE,
        "page_context": {"page_id": "buyer-overview"},
        "scout_report": {"status": "COMPLETE"},
        "budget_usage": {"total_tokens": 100},
    }
    assert save_task_snapshot(original, safe_node="代码侦察")["status"] == "saved"
    before = task_path(branch, tmp_path).read_text(encoding="utf-8")

    skipped = save_task_snapshot({
        "execution_mode": "write",
        "agent_branch": branch,
        "workbench_root": str(tmp_path),
        "task_snapshot": {},
        "current_page": None,
    }, safe_node="写入预检")
    after = task_path(branch, tmp_path).read_text(encoding="utf-8")
    assert skipped["status"] == "skipped_before_task_restore"
    assert after == before


def test_continuation_budget_is_task_bounded():
    assert continuation_allowed({
        "task_tokens_before_run": 4_000_000,
        "budget_usage": {"total_tokens": 5_000_001},
        "continuation_runs": 1,
    })
    assert not continuation_allowed({
        "task_tokens_before_run": 10_000_000,
        "budget_usage": {"total_tokens": 5_000_001},
        "continuation_runs": 2,
    })
    assert DEFAULT_POLICY.max_task_tokens == 15_000_000
