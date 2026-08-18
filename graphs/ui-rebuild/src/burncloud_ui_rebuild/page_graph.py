from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import builder_agent, fixer, reviewer, verifier
from .state import UIRebuildState


def _after_review(state: UIRebuildState) -> str:
    return "done" if not state.get("review_findings") else "fix"


def build_page_graph():
    builder = StateGraph(UIRebuildState)
    builder.add_node("builder", builder_agent)
    builder.add_node("verifier", verifier)
    builder.add_node("reviewer", reviewer)
    builder.add_node("fixer", fixer)

    builder.add_edge(START, "builder")
    builder.add_edge("builder", "verifier")
    builder.add_edge("verifier", "reviewer")
    builder.add_conditional_edges(
        "reviewer",
        _after_review,
        {"done": END, "fix": "fixer"},
    )
    builder.add_edge("fixer", "verifier")
    return builder.compile()
