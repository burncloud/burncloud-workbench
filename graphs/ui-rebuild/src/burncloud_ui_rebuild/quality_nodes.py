from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from .coding_tools import changed_source_files, create_page_checkpoint, run_named_validation
from .engineering_agents import run_v1_fixer_agent, run_v1_reviewer_agent
from .engineering_nodes import accumulate_usage, apply_budget_guard
from .policy import DEFAULT_POLICY, blocking_findings
from .state import Finding, UIRebuildState


def _changed_files(state: UIRebuildState) -> list[str]:
    if state.get("execution_mode", "dry_run") != "write":
        return []
    return changed_source_files(state["source_repo_root"])


def code_verifier(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    findings = list(state.get("verification_findings", []))
    results: list[dict[str, Any]] = []
    if page is None:
        return {"verification_findings": findings, "validation_results": results}

    if not (page["route"] == "/console" or page["route"].startswith("/console/")):
        findings.append(Finding(severity="blocker", code="CONSOLE_NAMESPACE", message="Management page escaped /console namespace.", evidence=page["route"], expected="/console/*"))

    contract = Path(state["workbench_root"]) / page["contract_path"]
    if not contract.exists():
        findings.append(Finding(severity="blocker", code="MISSING_PAGE_CONTRACT", message=f'Missing page contract for {page["id"]}.', expected=page["contract_path"]))

    if state.get("execution_mode", "dry_run") == "write":
        for name in DEFAULT_POLICY.code_validations:
            result = run_named_validation(state["source_repo_root"], name)
            results.append(result)
            if result["returncode"] != 0:
                findings.append(Finding(severity="blocker", code=f"VALIDATION_{name.upper()}", message=f"Code validation failed: {name}", evidence=str(result["output"]), expected="returncode 0"))

    update: dict[str, Any] = {
        "verification_findings": findings,
        "validation_results": results,
        "changed_files": _changed_files(state),
        "current_page_status": "code_verified" if not blocking_findings(findings) else "verification_failed",
    }
    return apply_budget_guard(state, update)


def reality_anchor(state: UIRebuildState) -> dict[str, Any]:
    findings = list(state.get("verification_findings", []))
    results = list(state.get("validation_results", []))

    if state.get("execution_mode", "dry_run") == "write":
        for name in DEFAULT_POLICY.reality_validations:
            result = run_named_validation(state["source_repo_root"], name)
            results.append(result)
            if result["returncode"] != 0:
                findings.append(Finding(severity="blocker", code=f"REALITY_{name.upper()}", message=f"Reality anchor failed: {name}", evidence=str(result["output"]), expected="returncode 0"))

    report = {
        "deterministic_validations": list(DEFAULT_POLICY.reality_validations),
        "browser_e2e": "capability_missing_not_silently_passed",
        "note": "BurnCloud currently has no repository browser-E2E suite for this Harness to invoke deterministically.",
    }
    update: dict[str, Any] = {
        "verification_findings": findings,
        "validation_results": results,
        "reality_report": report,
        "changed_files": _changed_files(state),
        "current_page_status": "reality_passed" if not blocking_findings(findings) else "reality_failed",
    }
    return apply_budget_guard(state, update)


def policy_reviewer(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {"review_findings": []}
    if state.get("execution_mode", "dry_run") == "dry_run":
        return {"review_findings": [], "review_summary": "Dry-run review.", "current_page_status": "review_passed"}

    report = run_v1_reviewer_agent(
        model_name=state["model_name"],
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
        scout_report=state.get("scout_report", {}),
        implementation_plan=state.get("implementation_plan", {}),
        verification_findings=list(state.get("verification_findings", [])),
        changed_files=list(state.get("changed_files", [])),
    )
    usage = dict(report.pop("_usage", {}))
    findings = [Finding(**item) for item in report.get("findings", [])]
    blocking = blocking_findings(findings)
    warnings = list(state.get("warnings", []))
    if findings and not blocking:
        warnings.append("Reviewer returned only minor/info findings; v1 policy passes with warnings.")

    update: dict[str, Any] = {
        "review_findings": findings,
        "review_summary": str(report.get("summary", "")),
        "warnings": warnings,
        "current_page_status": "review_failed" if blocking else ("review_passed_with_warnings" if findings else "review_passed"),
    }
    update.update(accumulate_usage(state, usage))
    return apply_budget_guard(state, update)


def policy_fixer(state: UIRebuildState) -> dict[str, Any]:
    current = state.get("fix_round", 0) + 1
    max_rounds = state.get("max_fix_rounds", DEFAULT_POLICY.max_fix_rounds)
    if current > max_rounds:
        page = state.get("current_page")
        page_id = page["id"] if page else "unknown"
        warnings = list(state.get("warnings", []))
        warnings.append(f"Fix loop exhausted after {max_rounds} rounds for {page_id}; escalating to human review.")
        return {"fix_round": max_rounds, "current_page_status": "fix_exhausted", "changed_files": _changed_files(state), "warnings": warnings}

    if state.get("execution_mode", "dry_run") == "dry_run":
        return {"fix_round": current, "verification_findings": [], "review_findings": [], "current_page_status": "fix_applied_dry_run"}

    page = state.get("current_page")
    if page is None:
        return {"fix_round": current}

    dirty_files = _changed_files(state)
    report = run_v1_fixer_agent(
        model_name=state["model_name"],
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        agent_branch=state["agent_branch"],
        page=page,
        implementation_plan=state.get("implementation_plan", {}),
        verification_findings=list(state.get("verification_findings", [])),
        review_findings=list(state.get("review_findings", [])),
        restore_files=dirty_files,
    )
    usage = dict(report.pop("_usage", {}))
    status = "fix_applied" if report["status"] == "COMPLETE" else "fix_blocked"
    update: dict[str, Any] = {
        "fix_round": current,
        "fixer_report": report,
        "changed_files": _changed_files(state),
        "current_page_status": status,
    }
    update.update(accumulate_usage(state, usage))
    if status == "fix_applied":
        update["verification_findings"] = []
        update["review_findings"] = []
    return apply_budget_guard(state, update)


def page_checkpoint(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    if state.get("execution_mode", "dry_run") != "write":
        return {"page_checkpoint": {"status": "dry_run", "page_id": page["id"]}}

    checkpoint = create_page_checkpoint(state["source_repo_root"], page["id"])
    history = list(state.get("page_checkpoint_history", []))
    history.append(checkpoint)
    page_context = dict(state.get("page_context", {}))
    page_context["checkpoint_commit"] = str(checkpoint.get("commit", ""))
    return {
        "page_checkpoint": checkpoint,
        "page_checkpoint_history": history,
        "page_context": page_context,
        "changed_files": _changed_files(state),
        "phase": "page_checkpointed",
    }


def human_review_gate(state: UIRebuildState) -> dict[str, Any]:
    decision = interrupt({
        "type": "burncloud_graph_engineering_v1_final_gate",
        "execution_mode": state.get("execution_mode", "write"),
        "model_name": state.get("model_name", ""),
        "policy": {
            "max_fix_rounds": DEFAULT_POLICY.max_fix_rounds,
            "max_plan_rounds": DEFAULT_POLICY.max_plan_rounds,
            "max_page_seconds": DEFAULT_POLICY.max_page_seconds,
            "max_run_seconds": DEFAULT_POLICY.max_run_seconds,
            "max_page_tokens": DEFAULT_POLICY.max_page_tokens,
            "max_run_tokens": DEFAULT_POLICY.max_run_tokens,
            "blocking_review_severities": sorted(DEFAULT_POLICY.blocking_review_severities),
            "page_write_prefixes": list(DEFAULT_POLICY.page_write_prefixes),
        },
        "run_context": state.get("run_context", {}),
        "page_context": state.get("page_context", {}),
        "budget_usage": state.get("budget_usage", {}),
        "invocation_history": state.get("invocation_history", []),
        "recovery_result": state.get("recovery_result", {}),
        "base_commit": state.get("base_commit", ""),
        "agent_branch": state.get("agent_branch", ""),
        "source_repo_root": state.get("source_repo_root", ""),
        "branch_reused": state.get("branch_reused", state.get("worktree_reused", False)),
        "branch_task_status": state.get("branch_task_status", "active"),
        "current_page": state.get("current_page"),
        "current_page_status": state.get("current_page_status", ""),
        "scout_report": state.get("scout_report", {}),
        "implementation_plan": state.get("implementation_plan", {}),
        "plan_findings": state.get("plan_findings", []),
        "fix_round": state.get("fix_round", 0),
        "last_failure_reason": state.get("last_failure_reason", ""),
        "fixer_report": state.get("fixer_report", {}),
        "verification_findings": state.get("verification_findings", []),
        "review_findings": state.get("review_findings", []),
        "validation_results": state.get("validation_results", []),
        "reality_report": state.get("reality_report", {}),
        "page_checkpoint": state.get("page_checkpoint", {}),
        "page_checkpoint_history": state.get("page_checkpoint_history", []),
        "completed_pages": len(state.get("completed_pages", [])),
        "total_pages": len(state.get("page_queue", [])),
        "changed_files": state.get("changed_files", []),
        "warnings": state.get("warnings", []),
        "final_findings": state.get("final_findings", []),
        "question": "Approve this bounded v1 UI engineering run for release processing?",
    })
    return {"human_decision": bool(decision)}
