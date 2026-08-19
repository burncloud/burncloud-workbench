from __future__ import annotations

from typing import Any

from .manifest import TARGET_PAGES
from .permissions import validate_target_manifest
from .policy import blocking_findings
from .state import Finding, UIRebuildState


BLOCKED_PAGE_STATUSES = {
    "scout_blocked",
    "plan_blocked",
    "plan_rejected",
    "builder_blocked",
    "scope_failed",
    "verification_failed",
    "reality_failed",
    "review_failed",
    "budget_exhausted",
    "fix_exhausted",
    "fix_blocked",
}


def final_quality_check(state: UIRebuildState) -> dict[str, Any]:
    """Final deterministic gate for v1; no LLM may waive these findings."""
    findings = list(validate_target_manifest(state.get("page_queue", TARGET_PAGES)))
    completed = set(state.get("completed_pages", []))
    expected = {task["id"] for task in state.get("page_queue", TARGET_PAGES)}
    missing = sorted(expected - completed)
    if missing:
        findings.append(Finding(
            severity="blocker",
            code="INCOMPLETE_PAGE_QUEUE",
            message=f"Not all target pages completed: {missing}",
            expected="Every page in this bounded run must pass before release processing.",
        ))

    status = state.get("current_page_status", "")
    if status in BLOCKED_PAGE_STATUSES:
        page = state.get("current_page")
        findings.append(Finding(
            severity="blocker",
            code="PAGE_ENGINEERING_BLOCKED",
            message=(
                f"Page engineering stopped with status {status}"
                + (f" for {page['id']}" if page else "")
                + "."
            ),
            evidence=state.get("last_failure_reason", "") or f"fix_round={state.get('fix_round', 0)}",
            expected="Resolve the bounded Scout/Plan/Scope/Validation/Review findings before release.",
        ))

    if blocking_findings(state.get("plan_findings", [])):
        findings.extend(state.get("plan_findings", []))
    if blocking_findings(state.get("verification_findings", [])):
        findings.extend(state.get("verification_findings", []))
    if blocking_findings(state.get("review_findings", [])):
        findings.extend(state.get("review_findings", []))

    # Deduplicate stable codes/messages while preserving order.
    unique: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        key = (finding.get("code", ""), finding.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return {"final_findings": unique, "phase": "final_quality_checked"}
