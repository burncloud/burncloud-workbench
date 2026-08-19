from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import builder_agent, fixer, reviewer, verifier
from .state import UIRebuildState


PAGE_NODE_BUILDER = "构建"
PAGE_NODE_VERIFIER = "验证"
PAGE_NODE_REVIEWER = "审查"
PAGE_NODE_FIXER = "修复"


def _after_review(state: UIRebuildState) -> str:
    return "完成" if not state.get("review_findings") else "修复"


def build_page_graph():
    builder = StateGraph(UIRebuildState)
    builder.add_node(PAGE_NODE_BUILDER, builder_agent)
    builder.add_node(PAGE_NODE_VERIFIER, verifier)
    builder.add_node(PAGE_NODE_REVIEWER, reviewer)
    builder.add_node(PAGE_NODE_FIXER, fixer)

    builder.add_edge(START, PAGE_NODE_BUILDER)
    builder.add_edge(PAGE_NODE_BUILDER, PAGE_NODE_VERIFIER)
    builder.add_edge(PAGE_NODE_VERIFIER, PAGE_NODE_REVIEWER)
    builder.add_conditional_edges(
        PAGE_NODE_REVIEWER,
        _after_review,
        {"完成": END, "修复": PAGE_NODE_FIXER},
    )
    builder.add_edge(PAGE_NODE_FIXER, PAGE_NODE_VERIFIER)
    return builder.compile()
