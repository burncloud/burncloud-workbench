from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AgentBudget:
    """Hard per-invocation budget for one LLM Agent node."""

    max_model_calls: int
    max_tool_calls: int
    model_timeout_seconds: int = 180


@dataclass(frozen=True)
class HarnessPolicy:
    """Central governance policy for the BurnCloud UI rebuild graph.

    Keep reliability decisions here instead of scattering magic numbers across
    prompts, tools and graph nodes. The model may reason inside these limits;
    Python owns the limits themselves.
    """

    default_page_limit: int = 1
    max_fix_rounds: int = 3
    max_write_files_per_agent: int = 8
    max_tool_output_chars: int = 40_000
    max_read_lines: int = 500
    blocking_review_severities: frozenset[str] = field(
        default_factory=lambda: frozenset({"blocker", "major"})
    )
    code_validations: tuple[str, ...] = ("cargo_fmt_check", "client_check")
    reality_validations: tuple[str, ...] = ("client_test",)
    builder_budget: AgentBudget = AgentBudget(max_model_calls=18, max_tool_calls=40)
    reviewer_budget: AgentBudget = AgentBudget(max_model_calls=10, max_tool_calls=24)
    fixer_budget: AgentBudget = AgentBudget(max_model_calls=12, max_tool_calls=28)


DEFAULT_POLICY = HarnessPolicy()


def finding_is_blocking(finding: Mapping[str, object]) -> bool:
    return str(finding.get("severity", "")).lower() in DEFAULT_POLICY.blocking_review_severities


def blocking_findings(findings: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [finding for finding in findings if finding_is_blocking(finding)]
