from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.graph import (
    _after_page_rebuild,
    build_graph,
    default_execution_mode,
    initial_state,
)
from burncloud_ui_rebuild.page_graph import (
    _after_fix,
    _capture_fix_context,
    _finalize_fix,
)


def test_default_execution_mode_is_write_and_page_limit_is_one():
    assert initial_state()["execution_mode"] == "write"
    assert initial_state()["page_limit"] == 1
    defaults = default_execution_mode({})
    assert defaults["execution_mode"] == "write"
    assert defaults["page_limit"] == 1


def test_explicit_dry_run_and_page_limit_are_preserved():
    defaults = default_execution_mode({"execution_mode": "dry_run", "page_limit": 7})
    assert defaults["execution_mode"] == "dry_run"
    assert defaults["page_limit"] == 7


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
            "verification_findings": [],
            "review_findings": [],
        }
    )

    assert after["verification_findings"] == before["verification_findings"]
    assert after["review_findings"] == before["review_findings"]
    assert "CLIENT_CHECK" in after["last_failure_reason"]
    assert after["fixer_report"]["status"] == "fix_blocked"


def test_blocked_page_is_not_marked_complete():
    assert _after_page_rebuild({"current_page_status": "fix_exhausted"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "fix_blocked"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "builder_blocked"}) == "人工介入"
    assert _after_page_rebuild({"current_page_status": "review_passed"}) == "页面完成"


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
