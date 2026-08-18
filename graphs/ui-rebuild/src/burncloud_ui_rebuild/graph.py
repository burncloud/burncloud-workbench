from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .config import source_root, workbench_root
from .nodes import (
    architecture_agent,
    final_permission_check,
    human_gate,
    mark_page_complete,
    permission_guardian,
    release_agent,
    repo_scout,
    select_next_page,
    spec_agent,
)
from .page_graph import build_page_graph
from .state import UIRebuildState


def _page_router(state: UIRebuildState) -> str:
    return "final_permission_check" if state.get("current_page") is None else "page_rebuild"


def _human_router(state: UIRebuildState) -> str:
    return "release" if state.get("human_decision") else "end"


def build_graph(checkpointer=None):
    page_rebuild = build_page_graph()
    builder = StateGraph(UIRebuildState)

    # Role 1 — Orchestrator is the parent graph itself. It schedules work but
    # does not invent product or permission decisions.
    builder.add_node("spec_agent", spec_agent)
    builder.add_node("repo_scout", repo_scout)
    builder.add_node("permission_guardian", permission_guardian)
    builder.add_node("architecture_agent", architecture_agent)
    builder.add_node("select_next_page", select_next_page)
    builder.add_node("page_rebuild", page_rebuild)
    builder.add_node("mark_page_complete", mark_page_complete)
    builder.add_node("final_permission_check", final_permission_check)
    builder.add_node("human_gate", human_gate)
    builder.add_node("release", release_agent)

    builder.add_edge(START, "spec_agent")
    builder.add_edge("spec_agent", "repo_scout")
    builder.add_edge("repo_scout", "permission_guardian")
    builder.add_edge("permission_guardian", "architecture_agent")
    builder.add_edge("architecture_agent", "select_next_page")

    builder.add_conditional_edges(
        "select_next_page",
        _page_router,
        {
            "page_rebuild": "page_rebuild",
            "final_permission_check": "final_permission_check",
        },
    )
    builder.add_edge("page_rebuild", "mark_page_complete")
    builder.add_edge("mark_page_complete", "select_next_page")
    builder.add_edge("final_permission_check", "human_gate")

    builder.add_conditional_edges(
        "human_gate",
        _human_router,
        {"release": "release", "end": END},
    )
    builder.add_edge("release", END)
    return builder.compile(checkpointer=checkpointer)


def initial_state(*, execution_mode: str = "dry_run", thread_id: str = "burncloud-ui-rebuild-v0.1") -> UIRebuildState:
    return {
        "thread_id": thread_id,
        "execution_mode": execution_mode,  # type: ignore[typeddict-item]
        "source_repo_root": str(source_root()),
        "workbench_root": str(workbench_root()),
        "max_fix_rounds": 3,
        "completed_pages": [],
        "implementation_results": [],
        "warnings": [],
        "phase": "start",
    }


# Development/Studio graph. Production should use a durable SQLite/Postgres checkpointer.
graph = build_graph(checkpointer=InMemorySaver())
