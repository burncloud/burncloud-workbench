from __future__ import annotations

import re

from langgraph.graph import END, START, StateGraph

from .coding_tools import normalize_repo_path
from .engineering_nodes import (
    page_scout_node,
    plan_guard_node,
    planned_builder_node,
    planner_node,
    scope_guard_node,
    start_page_context,
)
from .notifications import error_notifying_node
from .policy import DEFAULT_POLICY, blocking_findings
from .quality_nodes import code_verifier, page_formatter, policy_fixer, policy_reviewer, reality_anchor
from .state import Finding, UIRebuildState


PAGE_NODE_CONTEXT = "页面上下文"
PAGE_NODE_SCOUT = "代码侦察"
PAGE_NODE_PLANNER = "修改计划"
PAGE_NODE_PLAN_GUARD = "计划守卫"
PAGE_NODE_BUILDER = "实施修改"
PAGE_NODE_SCOPE_GUARD = "范围守卫"
PAGE_NODE_FORMATTER = "确定性格式化"
PAGE_NODE_POST_FORMAT_SCOPE = "格式化后范围复核"
PAGE_NODE_CODE_VERIFY = "代码验证"
PAGE_NODE_REALITY = "现实验证"
PAGE_NODE_REVIEWER = "独立审查"
PAGE_NODE_CAPTURE_FIX = "保存失败上下文"
PAGE_NODE_FIXER = "修复"
PAGE_NODE_FINALIZE_FIX = "整理修复结果"
PAGE_NODE_PREPARE_REPLAN = "准备重新规划"

_REPO_FILE_RE = re.compile(r"crates/client/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+")


def _after_scout(state: UIRebuildState) -> str:
    if state.get("current_page_status") in {"scout_blocked", "budget_exhausted"}:
        return "人工介入"
    return "规划"


def _after_plan_guard(state: UIRebuildState) -> str:
    if state.get("current_page_status") == "budget_exhausted":
        return "人工介入"
    if not state.get("plan_findings"):
        return "实施"
    if int(state.get("plan_round", 0)) < DEFAULT_POLICY.max_plan_rounds:
        return "重新规划"
    return "人工介入"


def _after_builder(state: UIRebuildState) -> str:
    if state.get("current_page_status") in {"builder_blocked", "budget_exhausted"}:
        return "人工介入"
    if state.get("execution_mode") == "dry_run":
        return "代码验证"
    return "范围检查"


def _scope_failure_route(state: UIRebuildState) -> str:
    blockers = blocking_findings(state.get("verification_findings", []))
    if not blockers:
        return ""

    # An unplanned page diff is first a planning mismatch, not a code-repair
    # problem. Give Planner a bounded chance to explicitly adopt or redesign the
    # scope before spending Fixer rounds. Other scope failures (for example stale
    # retry carry-over) remain Fixer-owned cleanup work.
    codes = {str(item.get("code", "")) for item in blockers}
    if (
        "SCOPE_GUARD_UNPLANNED_FILES" in codes
        and int(state.get("plan_round", 0)) < DEFAULT_POLICY.max_plan_rounds
    ):
        return "重新规划"
    return "修复"


def _after_scope_guard(state: UIRebuildState) -> str:
    failure = _scope_failure_route(state)
    return failure or "格式化"


def _after_formatter(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("verification_findings", [])) else "范围复核"


def _after_post_format_scope_guard(state: UIRebuildState) -> str:
    failure = _scope_failure_route(state)
    return failure or "代码验证"


def _after_code_verification(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("verification_findings", [])) else "现实验证"


def _after_reality_anchor(state: UIRebuildState) -> str:
    return "修复" if blocking_findings(state.get("verification_findings", [])) else "审查"


def _finding_repo_files(findings: list[dict[str, object]]) -> set[str]:
    """Extract concrete writable client files cited by structured findings.

    Reviewer evidence frequently contains locations such as
    `crates/client/src/app.rs:18-60`. A finding that proves correction requires a
    client file outside the approved allowlist is a planning mismatch; sending it
    to Fixer cannot work because Fixer is intentionally forbidden from expanding
    its own edit scope.
    """
    paths: set[str] = set()
    for item in findings:
        text = "\n".join(
            str(item.get(field, ""))
            for field in ("message", "evidence", "expected")
        )
        for match in _REPO_FILE_RE.findall(text):
            paths.add(normalize_repo_path(match))
    return paths


def _review_requires_replan(state: UIRebuildState, blockers: list[dict[str, object]]) -> bool:
    if int(state.get("plan_round", 0)) >= DEFAULT_POLICY.max_plan_rounds:
        return False
    allowed = {
        normalize_repo_path(str(path))
        for path in state.get("implementation_plan", {}).get("allowed_files", [])
    }
    cited = _finding_repo_files(blockers)
    return bool(cited - allowed)


def _after_review(state: UIRebuildState) -> str:
    if state.get("current_page_status") == "budget_exhausted":
        return "人工介入"
    blockers = list(blocking_findings(state.get("review_findings", [])))
    if not blockers:
        return "完成"
    if _review_requires_replan(state, blockers):
        return "重新规划"
    return "修复"


def _capture_fix_context(state: UIRebuildState) -> dict[str, object]:
    return {
        "last_verification_findings": list(state.get("verification_findings", [])),
        "last_review_findings": list(state.get("review_findings", [])),
    }


def _finalize_fix(state: UIRebuildState) -> dict[str, object]:
    status = state.get("current_page_status", "")
    if status not in {"fix_exhausted", "fix_blocked", "budget_exhausted"}:
        return {}

    fix_round = state.get("fix_round", 0)
    verification = list(state.get("verification_findings") or state.get("last_verification_findings", []))
    review = list(state.get("review_findings") or state.get("last_review_findings", []))
    combined = [*verification, *review]
    reason_parts = [
        f"{item.get('code', 'UNKNOWN')}: {item.get('message', '')}".strip()
        for item in combined
    ]
    last_failure_reason = "; ".join(part for part in reason_parts if part)
    if not last_failure_reason:
        last_failure_reason = state.get("last_failure_reason", "") or f"{status} after fix round {fix_round}."

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
    status = state.get("current_page_status")
    if status in {"fix_exhausted", "budget_exhausted"}:
        return "人工介入"
    if status == "fix_blocked":
        if int(state.get("plan_round", 0)) < DEFAULT_POLICY.max_plan_rounds:
            return "重新规划"
        return "人工介入"
    return "重新检查范围"


def _prepare_replan(state: UIRebuildState) -> dict[str, object]:
    """Turn unresolved quality blockers into deterministic Planner feedback.

    Fixer never gains permission to expand its own file scope. If a blocker cannot
    be solved inside the approved plan, the graph gives Planner one bounded chance
    to revise the plan. A new plan gets a fresh Fixer budget; plan_round bounds
    scope evolution while fix_round bounds repairs inside one approved plan.
    """
    verification = list(state.get("verification_findings") or state.get("last_verification_findings", []))
    review = list(state.get("review_findings") or state.get("last_review_findings", []))
    blockers = [*blocking_findings(verification), *blocking_findings(review)]

    plan_findings: list[Finding] = []
    for item in blockers:
        code = str(item.get("code", "UNKNOWN"))
        message = str(item.get("message", ""))
        evidence = str(item.get("evidence", ""))
        expected = str(item.get("expected", ""))
        plan_findings.append(Finding(
            severity="major",
            code=f"REPLAN_{code}",
            message=(
                "Previous approved plan could not resolve this blocking quality finding. "
                f"Revise the smallest client-side plan needed to address {code}: {message}"
            ),
            evidence=evidence,
            expected=expected or "Expand or revise allowed_files only when required by evidence; keep the plan bounded.",
        ))

    if not plan_findings:
        plan_findings.append(Finding(
            severity="major",
            code="REPLAN_FIXER_BLOCKED",
            message="Fixer returned BLOCKED inside the current approved plan; Planner must revise the bounded implementation plan.",
            evidence=str(state.get("fixer_report", {}).get("summary", "")),
            expected="Produce a revised client-only plan or explicitly report an unresolvable backend/product gap.",
        ))

    return {
        "plan_findings": plan_findings,
        "verification_findings": [],
        "review_findings": [],
        "fix_round": 0,
        "current_page_status": "replan_requested",
        "last_failure_reason": state.get("last_failure_reason", ""),
    }


def _add_safe_node(builder: StateGraph, name: str, node) -> None:
    builder.add_node(name, error_notifying_node(name, node))


def build_page_graph():
    builder = StateGraph(UIRebuildState)
    _add_safe_node(builder, PAGE_NODE_CONTEXT, start_page_context)
    _add_safe_node(builder, PAGE_NODE_SCOUT, page_scout_node)
    _add_safe_node(builder, PAGE_NODE_PLANNER, planner_node)
    _add_safe_node(builder, PAGE_NODE_PLAN_GUARD, plan_guard_node)
    _add_safe_node(builder, PAGE_NODE_BUILDER, planned_builder_node)
    _add_safe_node(builder, PAGE_NODE_SCOPE_GUARD, scope_guard_node)
    _add_safe_node(builder, PAGE_NODE_FORMATTER, page_formatter)
    _add_safe_node(builder, PAGE_NODE_POST_FORMAT_SCOPE, scope_guard_node)
    _add_safe_node(builder, PAGE_NODE_CODE_VERIFY, code_verifier)
    _add_safe_node(builder, PAGE_NODE_REALITY, reality_anchor)
    _add_safe_node(builder, PAGE_NODE_REVIEWER, policy_reviewer)
    _add_safe_node(builder, PAGE_NODE_CAPTURE_FIX, _capture_fix_context)
    _add_safe_node(builder, PAGE_NODE_FIXER, policy_fixer)
    _add_safe_node(builder, PAGE_NODE_FINALIZE_FIX, _finalize_fix)
    _add_safe_node(builder, PAGE_NODE_PREPARE_REPLAN, _prepare_replan)

    builder.add_edge(START, PAGE_NODE_CONTEXT)
    builder.add_edge(PAGE_NODE_CONTEXT, PAGE_NODE_SCOUT)
    builder.add_conditional_edges(
        PAGE_NODE_SCOUT,
        _after_scout,
        {"规划": PAGE_NODE_PLANNER, "人工介入": END},
    )
    builder.add_edge(PAGE_NODE_PLANNER, PAGE_NODE_PLAN_GUARD)
    builder.add_conditional_edges(
        PAGE_NODE_PLAN_GUARD,
        _after_plan_guard,
        {"实施": PAGE_NODE_BUILDER, "重新规划": PAGE_NODE_PLANNER, "人工介入": END},
    )
    builder.add_conditional_edges(
        PAGE_NODE_BUILDER,
        _after_builder,
        {
            "范围检查": PAGE_NODE_SCOPE_GUARD,
            "代码验证": PAGE_NODE_CODE_VERIFY,
            "人工介入": END,
        },
    )
    builder.add_conditional_edges(
        PAGE_NODE_SCOPE_GUARD,
        _after_scope_guard,
        {
            "格式化": PAGE_NODE_FORMATTER,
            "修复": PAGE_NODE_CAPTURE_FIX,
            "重新规划": PAGE_NODE_PREPARE_REPLAN,
        },
    )
    builder.add_conditional_edges(
        PAGE_NODE_FORMATTER,
        _after_formatter,
        {"范围复核": PAGE_NODE_POST_FORMAT_SCOPE, "修复": PAGE_NODE_CAPTURE_FIX},
    )
    builder.add_conditional_edges(
        PAGE_NODE_POST_FORMAT_SCOPE,
        _after_post_format_scope_guard,
        {
            "代码验证": PAGE_NODE_CODE_VERIFY,
            "修复": PAGE_NODE_CAPTURE_FIX,
            "重新规划": PAGE_NODE_PREPARE_REPLAN,
        },
    )
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
        {
            "完成": END,
            "修复": PAGE_NODE_CAPTURE_FIX,
            "重新规划": PAGE_NODE_PREPARE_REPLAN,
            "人工介入": END,
        },
    )
    builder.add_edge(PAGE_NODE_CAPTURE_FIX, PAGE_NODE_FIXER)
    builder.add_edge(PAGE_NODE_FIXER, PAGE_NODE_FINALIZE_FIX)
    builder.add_conditional_edges(
        PAGE_NODE_FINALIZE_FIX,
        _after_fix,
        {
            "重新检查范围": PAGE_NODE_SCOPE_GUARD,
            "重新规划": PAGE_NODE_PREPARE_REPLAN,
            "人工介入": END,
        },
    )
    builder.add_edge(PAGE_NODE_PREPARE_REPLAN, PAGE_NODE_PLANNER)
    return builder.compile()
