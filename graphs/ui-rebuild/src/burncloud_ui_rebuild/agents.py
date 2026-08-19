from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from burncloud_ui_rebuild.coding_tools import build_coding_tools
from burncloud_ui_rebuild.model_factory import create_chat_model


PROBE_VALUE = "burncloud-agent-ready"
PROBE_TOOL_RESULT = f"probe:{PROBE_VALUE}"


class AgentFinding(BaseModel):
    severity: Literal["blocker", "major", "minor", "info"]
    code: str
    message: str
    evidence: str = ""
    expected: str = ""


class BuilderReport(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)


class ReviewerReport(BaseModel):
    decision: Literal["PASS", "FAIL"]
    summary: str
    findings: list[AgentFinding] = Field(default_factory=list)


class FixerReport(BaseModel):
    status: Literal["COMPLETE", "BLOCKED"]
    summary: str
    addressed_codes: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)


@tool
def agent_probe(value: str) -> str:
    """Return a deterministic probe value used to verify Agent tool calling."""
    return f"probe:{value}"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def build_probe_agent(model_name: str):
    """Build the smallest real create_agent() instance used for endpoint verification."""
    model = create_chat_model(model_name, timeout=60)
    return create_agent(
        model=model,
        tools=[agent_probe],
        system_prompt=(
            "You are the BurnCloud Agent connectivity probe. "
            "You MUST call the agent_probe tool exactly once with the value "
            f"'{PROBE_VALUE}'. After receiving the tool result, reply with AGENT_READY."
        ),
    )


def run_agent_check(model_name: str) -> dict[str, Any]:
    """Verify model invocation plus tool calling without reading or changing BurnCloud source."""
    agent = build_probe_agent(model_name)
    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "Run the required connectivity probe now. "
                    "Do not skip the tool call and do not perform any other task."
                ),
            }
        ]
    })

    messages = list(result.get("messages", []))
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    tool_called = any(PROBE_TOOL_RESULT in _message_text(message) for message in tool_messages)
    final_text = _message_text(messages[-1]) if messages else ""
    ready = tool_called and "AGENT_READY" in final_text

    return {
        "status": "PASS" if ready else "FAIL",
        "model": model_name,
        "tool_called": tool_called,
        "final_text": final_text,
    }


def _base_system_prompt(role: str) -> str:
    return f"""
You are the BurnCloud {role} Agent inside a controlled LangGraph software-delivery workflow.

Authority and truth rules:
- burncloud-workbench/docs/ui is approved TARGET truth.
- burncloud/burncloud source code is CURRENT source truth.
- Never claim a target capability already exists until you inspect and verify the source.
- Product authority order: approved product decision > product-standard > information-architecture > page contract > implementation proposal.
- All management UI must remain under /console/*.
- Buyer, Supplier and Admin are workspace roles, not assumed hierarchy levels.
- One account may simultaneously have buyer + supplier; persisted workspace preference never overrides current authorization.
- Never invent backend data, pricing, balances, supplier earnings, settlement data, or capabilities.

Engineering rules:
- Inspect before changing.
- Discover source paths with search_source/list_source_directory before reading a path that was not already returned by a tool or supplied as an approved known path.
- Never invent repository paths from module names. If a read/list/search returns NOT_FOUND, treat it as recoverable: discover the real path and retry.
- Make the smallest correct change for the assigned scope.
- The write toolset enforces a hard budget of at most 8 distinct files per Agent invocation. Do not work around that limit; report BLOCKED if the page genuinely requires broader work.
- Never run or request crate-wide/workspace-wide formatting. If formatting is required, call format_source_file only for Rust files you intentionally changed.
- Reuse existing BurnCloud components and patterns where they satisfy the target contract.
- Never access .git internals.
- Never commit, push, merge, publish, install packages, or execute arbitrary shell commands.
- Use only the provided tools.
- Do not expose secrets in output.
""".strip()


def run_builder_agent(
    *,
    model_name: str,
    source_root: str,
    workbench_root: str,
    agent_branch: str,
    page: dict[str, Any],
    architecture_plan: dict[str, Any],
    permission_findings: list[dict[str, Any]],
    allow_write: bool,
) -> dict[str, Any]:
    tools = build_coding_tools(
        source_root=source_root,
        workbench_root=workbench_root,
        allow_write=allow_write,
        expected_branch=agent_branch if allow_write else None,
    )
    model = create_chat_model(model_name, timeout=180)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=_base_system_prompt("Builder") + """

Builder-specific rules:
- Before implementation, read this page's Page Contract and the relevant product/IA/agent-execution standards.
- Inspect the real route, component, data source, and nearby shared components before editing.
- If a prerequisite foundation is missing, implement only the minimum prerequisite required by this page; do not rebuild unrelated pages.
- In write mode, use targeted replacement/create tools and inspect git_diff afterward.
- Format only Rust files you intentionally changed, using format_source_file one file at a time.
- Run cargo_fmt_check and client_check after source changes unless a concrete repository limitation blocks them.
- In read-only mode, produce a plan and report BLOCKED rather than pretending files changed.
""",
        response_format=ToolStrategy(BuilderReport),
    )

    prompt = {
        "task": "Implement the assigned BurnCloud UI page contract.",
        "write_enabled": allow_write,
        "agent_branch": agent_branch,
        "page": page,
        "architecture_plan": architecture_plan,
        "permission_findings": permission_findings,
        "required_contract_reads": [
            "docs/ui/product-standard.md",
            "docs/ui/information-architecture.md",
            "docs/ui/agent-execution.md",
            page["contract_path"],
        ],
    }
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, BuilderReport):
        raise RuntimeError("Builder Agent did not return a validated BuilderReport.")
    return report.model_dump()


def run_reviewer_agent(
    *,
    model_name: str,
    source_root: str,
    workbench_root: str,
    page: dict[str, Any],
    architecture_plan: dict[str, Any],
    verification_findings: list[dict[str, Any]],
    changed_files: list[str],
) -> dict[str, Any]:
    tools = build_coding_tools(
        source_root=source_root,
        workbench_root=workbench_root,
        allow_write=False,
    )
    model = create_chat_model(model_name, timeout=180)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=_base_system_prompt("Reviewer") + """

Reviewer-specific rules:
- You are independent from Builder and must never edit source.
- Read the assigned Page Contract and inspect the actual git diff/current source.
- Judge Product Contract, role boundary, state completeness, security/privacy, consistency, and regression risk.
- Deterministic verification findings are evidence and cannot be ignored.
- Treat unexpectedly broad diffs as a scope/regression risk, especially when unrelated pages or files changed.
- Do not redesign the page. Return PASS only when there are no blocker/major correctness findings for the assigned scope.
- Every FAIL finding must include severity, stable code, evidence, and expected correction.
""",
        response_format=ToolStrategy(ReviewerReport),
    )
    prompt = {
        "task": "Independently review the Builder result for this page.",
        "page": page,
        "architecture_plan": architecture_plan,
        "verification_findings": verification_findings,
        "changed_files": changed_files,
        "required_contract_reads": [
            "docs/ui/product-standard.md",
            "docs/ui/information-architecture.md",
            "docs/ui/review-checklist.md",
            page["contract_path"],
        ],
    }
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, ReviewerReport):
        raise RuntimeError("Reviewer Agent did not return a validated ReviewerReport.")
    return report.model_dump()


def run_fixer_agent(
    *,
    model_name: str,
    source_root: str,
    workbench_root: str,
    agent_branch: str,
    page: dict[str, Any],
    review_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    tools = build_coding_tools(
        source_root=source_root,
        workbench_root=workbench_root,
        allow_write=True,
        expected_branch=agent_branch,
    )
    model = create_chat_model(model_name, timeout=180)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=_base_system_prompt("Fixer") + """

Fixer-specific rules:
- Fix only the supplied Reviewer findings for the assigned page.
- Do not perform unrelated refactors or redesigns.
- Inspect the exact evidence before editing.
- Format only Rust files you intentionally changed, using format_source_file one file at a time.
- After edits, inspect git_diff and run the relevant allowlisted validations.
- If a finding cannot be fixed safely inside the assigned scope, report BLOCKED and explain the concrete gap.
""",
        response_format=ToolStrategy(FixerReport),
    )
    prompt = {
        "task": "Correct only the listed review findings.",
        "agent_branch": agent_branch,
        "page": page,
        "review_findings": review_findings,
        "page_contract": page["contract_path"],
    }
    result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
    report = result.get("structured_response")
    if not isinstance(report, FixerReport):
        raise RuntimeError("Fixer Agent did not return a validated FixerReport.")
    return report.model_dump()
