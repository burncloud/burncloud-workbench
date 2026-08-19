from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.graph import build_graph, default_execution_mode, initial_state


def test_default_execution_mode_is_write():
    assert initial_state()["execution_mode"] == "write"
    assert default_execution_mode({})["execution_mode"] == "write"


def test_explicit_dry_run_is_preserved():
    assert default_execution_mode({"execution_mode": "dry_run"})["execution_mode"] == "dry_run"


def test_dry_run_processes_all_pages_then_waits_for_human():
    state = initial_state(execution_mode="dry_run", thread_id="test-ui-rebuild")
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "test-ui-rebuild"}}

    result = graph.invoke(state, config=config)

    assert len(result["completed_pages"]) == 25
    assert "__interrupt__" in result

    resumed = graph.invoke(Command(resume=True), config=config)
    assert resumed["release_status"] == "dry_run_complete_no_git_write"


def test_dry_run_can_limit_scope_to_first_golden_page():
    state = initial_state(
        execution_mode="dry_run",
        thread_id="test-ui-rebuild-one-page",
        page_limit=1,
    )
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "test-ui-rebuild-one-page"}}

    result = graph.invoke(state, config=config)

    assert result["completed_pages"] == ["buyer-overview"]
    assert len(result["page_queue"]) == 1
    assert "__interrupt__" in result
