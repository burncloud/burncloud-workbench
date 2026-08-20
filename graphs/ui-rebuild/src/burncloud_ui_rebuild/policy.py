from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AgentBudget:
    """Hard per-invocation budget for one LLM Agent node."""

    max_model_calls: int
    max_tool_calls: int
    model_timeout_seconds: int = 180


@dataclass(frozen=True)
class HarnessPolicy:
    """Central governance policy for BurnCloud Graph Engineering Harness v1.

    Models may reason inside these limits. Python owns the limits, routing,
    validation requirements, protected scopes and escalation behavior.
    """

    default_page_limit: int = 1
    max_fix_rounds: int = 3
    max_plan_rounds: int = 2
    max_page_seconds: int = 2_400
    max_run_seconds: int = 7_200
    # A normal Studio run defaults to one page. Let that page use the full
    # five-million-token Graph budget rather than tripping an earlier page cap.
    max_page_tokens: int = 5_000_000
    max_run_tokens: int = 5_000_000
    max_agent_invocations_per_page: int = 12

    max_write_files_per_agent: int = 8
    max_plan_files: int = 8
    max_tool_output_chars: int = 40_000
    max_read_lines: int = 500
    page_write_prefixes: tuple[str, ...] = ("crates/client/",)

    blocking_review_severities: frozenset[str] = field(
        default_factory=lambda: frozenset({"blocker", "major"})
    )

    code_validations: tuple[str, ...] = (
        "cargo_fmt_check",
        "client_check",
    )
    reality_validations: tuple[str, ...] = (
        "client_test",
        "client_liveview_check",
        "application_integration_check",
    )

    # BurnCloud is a large Rust workspace. Keep per-role loop ceilings high enough
    # for real repository exploration while retaining the five-million-token Graph
    # budget, wall-clock budgets, invocation budget and deterministic Graph edges as
    # the outer safety boundaries.
    scout_budget: AgentBudget = AgentBudget(max_model_calls=90, max_tool_calls=240)
    planner_budget: AgentBudget = AgentBudget(max_model_calls=60, max_tool_calls=150)
    builder_budget: AgentBudget = AgentBudget(max_model_calls=120, max_tool_calls=300)
    reviewer_budget: AgentBudget = AgentBudget(max_model_calls=60, max_tool_calls=150)
    fixer_budget: AgentBudget = AgentBudget(max_model_calls=90, max_tool_calls=240)


DEFAULT_POLICY = HarnessPolicy()


def finding_is_blocking(finding: Mapping[str, object]) -> bool:
    return str(finding.get("severity", "")).lower() in DEFAULT_POLICY.blocking_review_severities


def blocking_findings(findings: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [finding for finding in findings if finding_is_blocking(finding)]


def path_is_page_writable(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or any(part in {"..", ".git"} for part in candidate.parts):
        return False
    return any(normalized.startswith(prefix) for prefix in DEFAULT_POLICY.page_write_prefixes)
