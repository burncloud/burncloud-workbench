from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .graph import _after_page_rebuild, _final_gate_router
from .page_graph import (
    _after_code_verification,
    _after_fix,
    _after_formatter,
    _after_page_entry,
    _after_review,
    _after_scope_guard,
)
from .policy import DEFAULT_POLICY


@dataclass(frozen=True)
class Scenario:
    name: str
    run: Callable[[], bool]


def _review_outside_plan_replans() -> bool:
    state = {
        "plan_round": 1,
        "implementation_plan": {"allowed_files": ["crates/client/src/critical_pages/buyer_overview.rs"]},
        "review_findings": [{
            "severity": "blocker",
            "code": "ROUTE-001",
            "message": "Buyer route missing",
            "evidence": "crates/client/src/app.rs:18-60",
        }],
    }
    return _after_review(state) == "重新规划"


def _run_budget_continues() -> bool:
    return _after_page_rebuild({
        "current_page_status": "budget_exhausted",
        "task_tokens_before_run": 4_000_000,
        "budget_usage": {"total_tokens": 5_000_001},
        "continuation_runs": 1,
    }) == "自动续跑"


def _task_budget_escalates() -> bool:
    return _after_page_rebuild({
        "current_page_status": "budget_exhausted",
        "task_tokens_before_run": 10_000_000,
        "budget_usage": {"total_tokens": 5_000_001},
        "continuation_runs": 2,
    }) == "人工介入"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("scope_unplanned_replans", lambda: _after_scope_guard({
        "verification_findings": [{"severity": "blocker", "code": "SCOPE_GUARD_UNPLANNED_FILES", "message": "outside plan"}],
        "plan_round": 1,
    }) == "重新规划"),
    Scenario("preexisting_dirty_goes_to_fixer", lambda: _after_scope_guard({
        "verification_findings": [{"severity": "blocker", "code": "SCOPE_GUARD_PREEXISTING_DIRTY", "message": "carry-over"}],
        "plan_round": 1,
    }) == "修复"),
    Scenario("fix_blocked_replans", lambda: _after_fix({"current_page_status": "fix_blocked", "plan_round": 1}) == "重新规划"),
    Scenario("fix_exhausted_escalates", lambda: _after_fix({"current_page_status": "fix_exhausted"}) == "人工介入"),
    Scenario("plan_limit_blocks_more_replan", lambda: _after_fix({
        "current_page_status": "fix_blocked",
        "plan_round": DEFAULT_POLICY.max_plan_rounds,
    }) == "人工介入"),
    Scenario("review_outside_plan_replans", _review_outside_plan_replans),
    Scenario("run_budget_continues", _run_budget_continues),
    Scenario("task_budget_escalates", _task_budget_escalates),
    Scenario("deterministic_format_passes_to_scope", lambda: _after_formatter({"verification_findings": []}) == "范围复核"),
    Scenario("validation_failure_goes_to_fixer", lambda: _after_code_verification({
        "verification_findings": [{"severity": "blocker", "code": "CLIENT_CHECK", "message": "compile"}],
    }) == "修复"),
    Scenario("persisted_plan_resumes_builder", lambda: _after_page_entry({"resume_page_stage": "build"}) == "继续施工"),
    Scenario("clean_autopilot_skips_human_gate", lambda: _final_gate_router({
        "autopilot_mode": True,
        "final_findings": [],
    }) == "自动批准"),
)


def run_scenarios() -> dict[str, object]:
    results = []
    for scenario in SCENARIOS:
        try:
            passed = bool(scenario.run())
            error = ""
        except Exception as exc:
            passed = False
            error = f"{type(exc).__name__}: {exc}"
        results.append({"name": scenario.name, "passed": passed, "error": error})
    failed = [item for item in results if not item["passed"]]
    return {
        "status": "PASS" if not failed else "FAIL",
        "passed": len(results) - len(failed),
        "total": len(results),
        "results": results,
    }
