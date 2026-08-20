from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from .coding_tools import build_coding_tools
from .model_factory import create_chat_model
from .policy import AgentBudget, DEFAULT_POLICY


class ScoutReport(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    relevant_files: list[str] = Field(default_factory=list)
    relevant_symbols: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    backend_gaps: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    file: str
    intent: str
    symbols: list[str] = Field(default_factory=list)


class ImplementationPlan(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    allowed_files: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    backend_gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class BuilderReport(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    severity: Literal["blocker", "major", "minor", "info"]
    code: str
    message: str
    evidence: str = ""
    expected: str = ""


class ReviewerReport(BaseModel):
    decision: Literal["PASS", "FAIL"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class FixerReport(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    addressed_codes: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)


def _middleware(budget: AgentBudget):
    return [
        ModelCallLimitMiddleware(run_limit=budget.max_model_calls, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=budget.max_tool_calls, exit_behavior="continue"),
    ]


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _usage(result: dict[str, Any], role: str) -> dict[str, Any]:
    messages = list(result.get("messages", []))
    model_calls = tool_calls = input_tokens = output_tokens = total_tokens = 0
    for message in messages:
        if isinstance(message, AIMessage):
            model_calls += 1
            usage = getattr(message, "usage_metadata", None) or {}
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
            reported_total = int(usage.get("total_tokens", 0) or 0)
            total_tokens += reported_total or int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        elif isinstance(message, ToolMessage):
            tool_calls += 1
    return {
        "role": role,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _last_text(result: dict[str, Any]) -> str:
    messages = list(result.get("messages", []))
    return _message_text(messages[-1]).strip() if messages else ""


def _system(role: str) -> str:
    return f"""
You are the BurnCloud {role} inside Graph Engineering Harness v1.

Truth hierarchy:
- burncloud-workbench/docs/ui is approved TARGET truth.
- burncloud/burncloud source is CURRENT implementation truth.
- Never invent backend capability, balances, pricing, Tier data, role data or runtime state.
- All management UI belongs under /console/*.
- Buyer, Supplier and Admin are independent workspace roles; buyer+supplier on one account is normal.

Graph discipline:
- You are one bounded node, not the whole software company.
- Read only the context required for your assigned role.
- Do not redo deterministic validation owned by later graph nodes.
- Do not expand scope beyond the structured task you receive.
- Missing source paths are recoverable discovery misses; search before guessing.
- Never access .git, never push, merge or publish, never execute arbitrary shell commands.
- Never expose secrets.
""".strip()


def run_page_scout_agent(*, model_name: str, source_root: str, workbench_root: str, page: dict[str, Any]) -> dict[str, Any]:
    budget = DEFAULT_POLICY.scout_budget
    tools = build_coding_tools(source_root=source_root, workbench_root=workbench_root, allow_write=False)
    agent = create_agent(
        model=create_chat_model(model_name, timeout=budget.model_timeout_seconds),
        tools=tools,
        middleware=_middleware(budget),
        system_prompt=_system("Page Scout") + """

Scout rules:
- Read the assigned Page Contract first.
- Discover the current route, layout, page component, data-loading functions and nearby shared components.
- Report exact relevant paths and important symbols. Do not design or edit.
- Explicitly separate backend gaps from frontend implementation gaps.
- Keep relevant_files small and evidence-driven.
""",
        response_format=ToolStrategy(ScoutReport),
    )
    prompt = {"task": "Discover the minimum current-source context needed to implement this page contract.", "page": page, "required_contract": page["contract_path"]}
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, ScoutReport):
        report = ScoutReport(status="BLOCKED", summary="Scout stopped before producing a validated report.", constraints=[_last_text(result) or "SCOUT_BUDGET_OR_EARLY_TERMINATION"])
    payload = report.model_dump()
    payload["_usage"] = _usage(result, "scout")
    return payload


def run_planner_agent(*, model_name: str, source_root: str, workbench_root: str, page: dict[str, Any], scout_report: dict[str, Any], previous_plan_findings: list[dict[str, Any]]) -> dict[str, Any]:
    budget = DEFAULT_POLICY.planner_budget
    tools = build_coding_tools(source_root=source_root, workbench_root=workbench_root, allow_write=False)
    agent = create_agent(
        model=create_chat_model(model_name, timeout=budget.model_timeout_seconds),
        tools=tools,
        middleware=_middleware(budget),
        system_prompt=_system("Implementation Planner") + f"""

Planner rules:
- Convert Scout evidence + Page Contract into the smallest implementation plan.
- Every writable file MUST appear in allowed_files before Builder starts.
- Plan at most {DEFAULT_POLICY.max_plan_files} files.
- Page implementation is client-scoped. Backend gaps must be reported, not papered over with fake frontend data.
- If scout_report contains preexisting_dirty_files from a retry, explicitly keep only relevant carry-over by listing it in allowed_files; omit unrelated carry-over so deterministic cleanup can restore it.
- Prefer truthful Unknown/Unavailable states when an approved backend source does not exist.
- Do not edit files.
""",
        response_format=ToolStrategy(ImplementationPlan),
    )
    prompt = {"task": "Produce a bounded implementation plan for this page.", "page": page, "scout_report": scout_report, "previous_plan_findings": previous_plan_findings}
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, ImplementationPlan):
        report = ImplementationPlan(status="BLOCKED", summary="Planner stopped before producing a validated plan.", risks=[_last_text(result) or "PLANNER_BUDGET_OR_EARLY_TERMINATION"])
    payload = report.model_dump()
    payload["_usage"] = _usage(result, "planner")
    return payload


def run_planned_builder_agent(*, model_name: str, source_root: str, workbench_root: str, agent_branch: str, page: dict[str, Any], scout_report: dict[str, Any], implementation_plan: dict[str, Any]) -> dict[str, Any]:
    budget = DEFAULT_POLICY.builder_budget
    allowed_files = list(implementation_plan.get("allowed_files", []))
    tools = build_coding_tools(
        source_root=source_root,
        workbench_root=workbench_root,
        allow_write=True,
        expected_branch=agent_branch,
        allowed_write_files=allowed_files,
        allowed_restore_files=[],
    )
    agent = create_agent(
        model=create_chat_model(model_name, timeout=budget.model_timeout_seconds),
        tools=tools,
        middleware=_middleware(budget),
        system_prompt=_system("Builder") + """

Builder rules:
- Implement the approved plan; do not rediscover or redesign the whole page.
- You may edit ONLY files in allowed_files. Tool enforcement rejects anything else.
- Builder may not restore retry carry-over to HEAD; cleanup ownership belongs to Fixer after Scope Guard.
- If the plan is insufficient, return BLOCKED rather than expanding scope.
- Use exact targeted edits; format only Rust files intentionally changed.
- Inspect git_diff before finishing, but leave fmt/check/test to deterministic graph nodes.
""",
        response_format=ToolStrategy(BuilderReport),
    )
    prompt = {"task": "Implement exactly the approved plan for this page.", "page": page, "scout_report": scout_report, "implementation_plan": implementation_plan}
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, BuilderReport):
        report = BuilderReport(status="BLOCKED", summary="Builder stopped before producing a validated report.", known_gaps=[_last_text(result) or "BUILDER_BUDGET_OR_EARLY_TERMINATION"])
    payload = report.model_dump()
    payload["_usage"] = _usage(result, "builder")
    return payload


def run_v1_reviewer_agent(*, model_name: str, source_root: str, workbench_root: str, page: dict[str, Any], scout_report: dict[str, Any], implementation_plan: dict[str, Any], verification_findings: list[dict[str, Any]], changed_files: list[str]) -> dict[str, Any]:
    budget = DEFAULT_POLICY.reviewer_budget
    tools = build_coding_tools(source_root=source_root, workbench_root=workbench_root, allow_write=False)
    agent = create_agent(
        model=create_chat_model(model_name, timeout=budget.model_timeout_seconds),
        tools=tools,
        middleware=_middleware(budget),
        system_prompt=_system("Independent Reviewer") + """

Reviewer rules:
- You are independent from Scout, Planner and Builder and never edit source.
- Read the Page Contract, approved plan and actual diff/current source.
- Deterministic findings are facts and cannot be waived.
- Check product contract, role/route boundaries, truthful unknown states, accessibility and regression/scope risk.
- blocker/major block completion. minor/info are advisory and must not create a repair loop.
- Stable findings require code, severity, evidence and expected correction.
""",
        response_format=ToolStrategy(ReviewerReport),
    )
    prompt = {"task": "Independently judge whether this page can pass the product gate.", "page": page, "scout_report": scout_report, "implementation_plan": implementation_plan, "verification_findings": verification_findings, "changed_files": changed_files}
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, ReviewerReport):
        report = ReviewerReport(
            decision="FAIL",
            summary="Reviewer stopped before producing a validated report.",
            findings=[Finding(severity="major", code="REVIEWER_BUDGET_OR_EARLY_TERMINATION", message="Reviewer did not finish inside its Agent budget.", evidence=_last_text(result), expected="Complete an independent bounded review.")],
        )
    payload = report.model_dump()
    payload["_usage"] = _usage(result, "reviewer")
    return payload


def run_v1_fixer_agent(
    *,
    model_name: str,
    source_root: str,
    workbench_root: str,
    agent_branch: str,
    page: dict[str, Any],
    implementation_plan: dict[str, Any],
    verification_findings: list[dict[str, Any]],
    review_findings: list[dict[str, Any]],
    restore_files: list[str],
) -> dict[str, Any]:
    budget = DEFAULT_POLICY.fixer_budget
    allowed_files = list(implementation_plan.get("allowed_files", []))
    tools = build_coding_tools(
        source_root=source_root,
        workbench_root=workbench_root,
        allow_write=True,
        expected_branch=agent_branch,
        allowed_write_files=allowed_files,
        allowed_restore_files=restore_files,
    )
    agent = create_agent(
        model=create_chat_model(model_name, timeout=budget.model_timeout_seconds),
        tools=tools,
        middleware=_middleware(budget),
        system_prompt=_system("Fixer") + """

Fixer rules:
- Fix only supplied blocking findings inside the already-approved plan scope.
- Do not add new editable files to the plan or perform unrelated refactors.
- Files outside allowed_files may only be discarded with restore_source_file when explicitly listed in restore_files because Scope Guard classified them as page-local pollution or unrelated retry carry-over.
- restore_source_file does NOT grant edit permission to that file.
- Never restore a carry-over file that Planner explicitly preserved in allowed_files.
- If a finding requires a backend capability not approved for this page graph, return BLOCKED and preserve the BackendGap.
- Leave deterministic validation to the graph after you finish.
""",
        response_format=ToolStrategy(FixerReport),
    )
    prompt = {
        "task": "Fix only the blocking findings while preserving plan scope.",
        "page": page,
        "implementation_plan": implementation_plan,
        "verification_findings": verification_findings,
        "review_findings": review_findings,
        "restore_files": restore_files,
    }
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, FixerReport):
        report = FixerReport(status="BLOCKED", summary="Fixer stopped before producing a validated report.", known_gaps=[_last_text(result) or "FIXER_BUDGET_OR_EARLY_TERMINATION"])
    payload = report.model_dump()
    payload["_usage"] = _usage(result, "fixer")
    return payload
