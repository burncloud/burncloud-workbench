from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import builder_agent
from .policy import blocking_findings
from .quality_nodes import code_verifier, policy_fixer, policy_reviewer, reality_anchor
from .state import UIRebuildState


PAGE_NODE_BUILDER = "构建"
PAGE_NODE_CODE_VERIFY = "代码验证"
PAGE_NODE_REALITY = "现实验证"
PAGE_NODE_REVIEWER = "审查"
PAGE_NODE_CAPTURE_FIX = "保存失败上下文"
PAGE_NODE_FIXER = "修复"
PAGE_NODE_FINALIZE_FIX = "整理修复结果"


def _after_code_verification(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("verification_findings", [])) else "现实验证"


def _after_reality_anchor(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("verification_findings", [])) else "审查"


def _after_review(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("review_findings", [])) else "完成"


def _capture_fix_context(state: UIRebuildState) -> dict[str, object]:
    """Snapshot the exact deterministic/reviewer findings before Fixer mutates state."""
    return {
        "last_verification_findings": list(state.get("verification_findings", [])),
        "last_review_findings": list(state.get("review_findings", [])),
    }


def _finalize_fix(state: UIRebuildState) -> dict[str, object]:
    """Restore the last failure context when Fixer blocks or exhausts retries."""
    status = state.get("current_page_status", "")
    if status not in {"fix_exhausted", "fix_blocked"}:
        return {}

    fix_round = state.get("fix_round", 0)
    verification = list(
        state.get("verification_findings")
        or state.get("last_verification_findings", [])
    )
    review = list(
        state.get("review_findings")
        or state.get("last_review_findings", [])
    )
    combined = [*verification, *review]
    reason_parts = [
        f"{item.get('code', 'UNKNOWN')}: {item.get('message', '')}".strip()
        for item in combined
    ]
    last_failure_reason = "; ".join(part for part in reason_parts if part)
    if not last_failure_reason:
        last_failure_reason = f"{status} after fix round {fix_round}; no structured finding was preserved."

    fixer_report = dict(state.get("fixer_report", {}))
    fixer_report.update({
        "status": status,
        "fix_round": fix_round,
        "preserved_verification_findings": verification,
        "preserved_review_findings": review,
    })
    return {
        "verification_findings": verification,
        "review_findings": review,
        "fixer_report": fixer_report,
        "last_failure_reason": last_failure_reason,
    }


def _after_fix(state: UIRebuildState) -> str:
    if state.get("current_page_status") in {"fix_exhausted", "fix_blocked"}:
        return "人工介入"
    return "重新验证"


def build_page_graph():
    builder = StateGraph(UIRebuildState)
    builder.add_node(PAGE_NODE_BUILDER, builder_agent)
    builder.add_node(PAGE_NODE_CODE_VERIFY, code_verifier)
    builder.add_node(PAGE_NODE_REALITY, reality_anchor)
    builder.add_node(PAGE_NODE_REVIEWER, policy_reviewer)
    builder.add_node(PAGE_NODE_CAPTURE_FIX, _capture_fix_context)
    builder.add_node(PAGE_NODE_FIXER, policy_fixer)
    builder.add_node(PAGE_NODE_FINALIZE_FIX, _finalize_fix)

    builder.add_edge(START, PAGE_NODE_BUILDER)
    builder.add_edge(PAGE_NODE_BUILDER, PAGE_NODE_CODE_VERIFY)
    builder.add_conditional_edges(
        PAGE_NODE_CODE_VERIFY,
        _after_code_verification,
        {"现实验证": PAGE_NODE_REALITY, "修复": PAGE_NODE_CAPTURE_FIX},
    )
    builder.add_conditional_edges(
        PAGE_NODE_REALITY,
        _after_reality_anchor,
        {"审查": PAGE_NODE_REVIEWER, "修复": PAGE_NODE_CAPTURE_FIX},
    )
    builder.add_conditional_edges(
        PAGE_NODE_REVIEWER,
        _after_review,
        {"完成": END, "修复": PAGE_NODE_CAPTURE_FIX},
    )
    builder.add_edge(PAGE_NODE_CAPTURE_FIX, PAGE_NODE_FIXER)
    builder.add_edge(PAGE_NODE_FIXER, PAGE_NODE_FINALIZE_FIX)
    builder.add_conditional_edges(
        PAGE_NODE_FINALIZE_FIX,
        _after_fix,
        {"重新验证": PAGE_NODE_CODE_VERIFY, "人工介入": END},
    )
    return builder.compile()
