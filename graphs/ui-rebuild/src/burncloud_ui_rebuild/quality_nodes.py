from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import run_fixer_agent
from .coding_tools import changed_source_files, create_page_checkpoint, run_named_validation
from .nodes import reviewer as base_reviewer
from .policy import DEFAULT_POLICY, blocking_findings
from .state import Finding, UIRebuildState


def code_verifier(state: UIRebuildState) -> dict[str, Any]:
    """Deterministic code-level gate owned entirely by HarnessPolicy."""
    page = state.get("current_page")
    findings: list[Finding] = []
    results: list[dict[str, Any]] = []
    if page is None:
        return {"verification_findings": findings, "validation_results": results}

    if not (page["route"] == "/console" or page["route"].startswith("/console/")):
        findings.append(Finding(
            severity="blocker",
            code="CONSOLE_NAMESPACE",
            message="Management page escaped /console namespace.",
            evidence=page["route"],
            expected="/console/*",
        ))

    contract = Path(state["workbench_root"]) / page["contract_path"]
    if not contract.exists():
        findings.append(Finding(
            severity="blocker",
            code="MISSING_PAGE_CONTRACT",
            message=f'Missing page contract for {page["id"]}.',
            expected=page["contract_path"],
        ))

    if state.get("execution_mode", "dry_run") == "write":
        for name in DEFAULT_POLICY.code_validations:
            result = run_named_validation(state["source_repo_root"], name)
            results.append(result)
            if result["returncode"] != 0:
                findings.append(Finding(
                    severity="blocker",
                    code=f"VALIDATION_{name.upper()}",
                    message=f"Code validation failed: {name}",
                    evidence=str(result["output"]),
                    expected="returncode 0",
                ))

    return {
        "verification_findings": findings,
        "validation_results": results,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "code_verified" if not blocking_findings(findings) else "verification_failed",
    }


def reality_anchor(state: UIRebuildState) -> dict[str, Any]:
    """Deterministic runtime-adjacent anchor that executes real client tests.

    This intentionally lives outside the LLM loop. A model cannot waive or reinterpret
    a failing test; it must return to Fixer through graph routing.
    """
    findings = list(state.get("verification_findings", []))
    results = list(state.get("validation_results", []))

    if state.get("execution_mode", "dry_run") == "write":
        for name in DEFAULT_POLICY.reality_validations:
            result = run_named_validation(state["source_repo_root"], name)
            results.append(result)
            if result["returncode"] != 0:
                findings.append(Finding(
                    severity="blocker",
                    code=f"REALITY_{name.upper()}",
                    message=f"Reality anchor failed: {name}",
                    evidence=str(result["output"]),
                    expected="returncode 0",
                ))

    return {
        "verification_findings": findings,
        "validation_results": results,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "reality_passed" if not blocking_findings(findings) else "reality_failed",
    }


def policy_reviewer(state: UIRebuildState) -> dict[str, Any]:
    """Independent reviewer with severity policy applied by Python, not by the LLM."""
    result = dict(base_reviewer(state))
    findings = list(result.get("review_findings", []))
    blocking = blocking_findings(findings)
    warnings = list(state.get("warnings", []))

    if findings and not blocking:
        warnings.append(
            "Reviewer returned only minor/info findings; HarnessPolicy allows completion with warnings."
        )
        status = "review_passed_with_warnings"
    elif blocking:
        status = "review_failed"
    else:
        status = "review_passed"

    result["current_page_status"] = status
    result["warnings"] = warnings
    return result


def policy_fixer(state: UIRebuildState) -> dict[str, Any]:
    """Bounded Fixer that consumes both deterministic and reviewer findings."""
    current = state.get("fix_round", 0) + 1
    max_rounds = state.get("max_fix_rounds", DEFAULT_POLICY.max_fix_rounds)
    if current > max_rounds:
        page = state.get("current_page")
        page_id = page["id"] if page else "unknown"
        warnings = list(state.get("warnings", []))
        warnings.append(
            f"Fix loop exhausted after {max_rounds} rounds for {page_id}; escalating to human review."
        )
        return {
            "fix_round": max_rounds,
            "current_page_status": "fix_exhausted",
            "changed_files": changed_source_files(state["source_repo_root"]),
            "warnings": warnings,
        }

    if state.get("execution_mode", "dry_run") == "dry_run":
        return {
            "fix_round": current,
            "verification_findings": [],
            "review_findings": [],
            "current_page_status": "fix_applied_dry_run",
        }

    page = state.get("current_page")
    if page is None:
        return {"fix_round": current}

    report = run_fixer_agent(
        model_name=state.get("model_name", ""),
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        agent_branch=state["agent_branch"],
        page=page,
        verification_findings=list(state.get("verification_findings", [])),
        review_findings=list(state.get("review_findings", [])),
    )
    status = "fix_applied" if report["status"] == "COMPLETE" else "fix_blocked"
    update: dict[str, Any] = {
        "fix_round": current,
        "fixer_report": report,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": status,
    }
    if status == "fix_applied":
        update["verification_findings"] = []
        update["review_findings"] = []
    return update


def page_checkpoint(state: UIRebuildState) -> dict[str, Any]:
    """Create a local Git checkpoint after a page passes all quality gates."""
    page = state.get("current_page")
    if page is None:
        return {}
    if state.get("execution_mode", "dry_run") != "write":
        return {
            "page_checkpoint": {
                "status": "dry_run",
                "page_id": page["id"],
            }
        }

    checkpoint = create_page_checkpoint(state["source_repo_root"], page["id"])
    history = list(state.get("page_checkpoint_history", []))
    history.append(checkpoint)
    return {
        "page_checkpoint": checkpoint,
        "page_checkpoint_history": history,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "phase": "page_checkpointed",
    }
