from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.graph import build_graph, initial_state


def test_dry_run_processes_all_pages_then_waits_for_human():
    state = initial_state(execution_mode="dry_run", thread_id="test-ui-rebuild")
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "test-ui-rebuild"}}

    result = graph.invoke(state, config=config)

    assert len(result["completed_pages"]) == 25
    assert "__interrupt__" in result

    resumed = graph.invoke(Command(resume=True), config=config)
    assert resumed["release_status"] == "dry_run_complete_no_git_write"
