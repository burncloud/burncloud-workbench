from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.graph import (
    _after_page_rebuild,
    build_graph,
    default_execution_mode,
    initial_state,
)
from burncloud_ui_rebuild.page_graph import (
    _after_code_verification,
    _after_fix,
    _after_reality_anchor,
    _after_review,
    _capture_fix_context,
    _finalize_fix,
)
from burncloud_ui_rebuild.policy import DEFAULT_POLICY, blocking_findings


def test_default_execution_mode_is_write_and_page_limit_is_one():
    assert initial_state()["execution_mode"] == "write"
    assert initial_state()["page_limit"] == DEFAULT_POLICY.default_page_limit == 1
    defaults = default_execution_mode({})
    assert defaults["execution_mode"] == "write"
    assert defaults["page_limit"] == 1
    assert defaults["max_fix_rounds"] == DEFAULT_POLICY.max_fix_rounds


def test_explicit_dry_run_and_page_limit_are_preserved():
    defaults = default_execution_mode({"execution_mode": "dry_run", "page_limit": 7})
    assert defaults["execution_mode"] == "dry_run"
    assert defaults["page_limit"] == 7


def test_only_major_and_blocker_findings_trigger_repair():
    minor = [{"severity": "minor", "code": "COPY", "message": "wording polish"}]
    info = [{"severity": "info", "code": "NOTE", "message": "future idea"}]
    major = [{"severity": "major", "code": "ROLE", "message": "wrong role boundary"}]
    blocker = [{"severity": "blocker", "code": "BUILD", "message": "compile failed"}]

    assert blocking_findings(minor) == []
    assert blocking_findings(info) == []
    assert len(blocking_findings(major)) == 1
    assert len(blocking_findings(blocker)) == 1
    assert _after_review({"review_findings": minor}) == "完成"
    assert _after_review({"review_findings": info}) == "完成"
    assert _after_review({"review_findings": major}) == "修复"
    assert _after_review({"review_findings": blocker}) == "修复"


def test_deterministic_quality_failures_route_directly_to_fixer():
    blocker = [{"severity": "blocker", "code": "CHECK", "message": "failed"}]
    assert _after_code_verification({"verification_findings": blocker}) == "修复"
    assert _after_reality_anchor({"verification_findings": blocker}) == "修复"
    assert _after_code_verification({"verification_findings": []}) == "现实验证"
    assert _after_reality_anchor({"verification_findings": []}) == "审查"


def test_exhausted_or_blocked_fix_routes_to_human_intervention():
    assert _after_fix({"current_page_status": "fix_exhausted"}) == "人工介入"
    assert _after_fix({"current_page_status": "fix_blocked"}) == "人工介入"
    assert _after_fix({"current_page_status": "fix_applied"}) == "重新验证"


def test_fix_context_is_preserved_when_fixer_blocks():
    before = {
        "verification_findings": [
            {"severity": "blocker", "code": "CLIENT_CHECK", "message": "cargo check failed"}
        ],
        "review_findings": [
            {"severity": "major", "code": "BUYER_OVERVIEW", "message": "missing required state"}
        ],
    }
    snapshot = _capture_fix_context(before)
    after = _finalize_fix(
        {
            **snapshot,
            "current_page_status": "fix_blocked",
            "fix_round": 1,
            "fixer_report": {"summary": "blocked"},
            "verification_findings": [],
            "review_findings": [],
        }
    )

    assert after["verification_findings"] == before["verification_findings"]
    assert after["review_findings"] == before["review_findings"]
    assert "CLIENT_CHECK" in after["last_failure_reason"]
    assert after["fixer_report"]["status"] == "fix_blocked"
    assert after["fixer_report"]["summary"] == "blocked"


def test_blocked_page_is_not_checkpointed_or_marked_complete():
    assert _after_page_rebuild({"current_page_status": "fix_exhausted"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "fix_blocked"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "builder_blocked"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "review_passed"}) == "页面通过"
    assert _after_page_rebuild({"current_page_status": "review_passed_with_warnings"}) == "页面通过"


def test_dry_run_processes_all_pages_then_waits_for_human():
    state = initial_state(
        execution_mode="dry_run",
        thread_id="test-ui-rebuild",
        page_limit=25,
    )
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "test-ui-rebuild"}}

    result = graph.invoke(state, config=config)

    assert len(result["completed_pages"]) == 25
    assert "__interrupt__" in result

    resumed = graph.invoke(Command(resume=True), config=config)
    assert resumed["release_status"] == "dry_run_complete_no_git_write"


def test_dry_run_defaults_to_first_golden_page():
    state = initial_state(
        execution_mode="dry_run",
        thread_id="test-ui-rebuild-one-page",
    )
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "test-ui-rebuild-one-page"}}

    result = graph.invoke(state, config=config)

    assert result["completed_pages"] == ["buyer-overview"]
    assert len(result["page_queue"]) == 1
    assert "__interrupt__" in result
