from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .coding_tools import (
    changed_source_files,
    checkpoint_history,
    head_commit,
    normalize_repo_path,
    restore_page_checkpoint,
)
from .engineering_agents import run_page_scout_agent, run_planned_builder_agent, run_planner_agent
from .policy import DEFAULT_POLICY, path_is_page_writable
from .state import Finding, UIRebuildState


def _usage_values(usage: dict[str, Any]) -> dict[str, int]:
    return {
        "model_calls": int(usage.get("model_calls", 0) or 0),
        "tool_calls": int(usage.get("tool_calls", 0) or 0),
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def accumulate_usage(state: UIRebuildState, usage: dict[str, Any]) -> dict[str, Any]:
    current = dict(state.get("budget_usage", {}))
    current["agent_invocations"] = int(current.get("agent_invocations", 0)) + 1
    current["page_agent_invocations"] = int(current.get("page_agent_invocations", 0)) + 1
    for key, value in _usage_values(usage).items():
        current[key] = int(current.get(key, 0)) + value
        current[f"page_{key}"] = int(current.get(f"page_{key}", 0)) + value
    return {
        "budget_usage": current,
        "invocation_history": [*state.get("invocation_history", []), dict(usage)],
    }


def budget_reason(state: UIRebuildState, *, now: float | None = None) -> str:
    usage = state.get("budget_usage", {})
    now = now if now is not None else time.time()
    run_started = float(usage.get("run_started_at", now))
    page_started = float(usage.get("page_started_at", now))
    if now - run_started > DEFAULT_POLICY.max_run_seconds:
        return f"Run wall-clock budget exceeded {DEFAULT_POLICY.max_run_seconds}s."
    if now - page_started > DEFAULT_POLICY.max_page_seconds:
        return f"Page wall-clock budget exceeded {DEFAULT_POLICY.max_page_seconds}s."
    if int(usage.get("total_tokens", 0)) > DEFAULT_POLICY.max_run_tokens:
        return f"Run token budget exceeded {DEFAULT_POLICY.max_run_tokens}."
    if int(usage.get("page_total_tokens", 0)) > DEFAULT_POLICY.max_page_tokens:
        return f"Page token budget exceeded {DEFAULT_POLICY.max_page_tokens}."
    if int(usage.get("page_agent_invocations", 0)) > DEFAULT_POLICY.max_agent_invocations_per_page:
        return f"Page Agent invocation budget exceeded {DEFAULT_POLICY.max_agent_invocations_per_page}."
    return ""


def apply_budget_guard(state: UIRebuildState, update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    merged.update(update)
    reason = budget_reason(merged)
    if not reason:
        return update
    usage = dict(merged.get("budget_usage", {}))
    usage["exhausted_reason"] = reason
    findings = list(merged.get("verification_findings", []))
    findings.append(Finding(
        severity="blocker",
        code="HARNESS_BUDGET_EXHAUSTED",
        message=reason,
        expected="Escalate or start a new bounded page run; do not continue the inner Agent loop.",
    ))
    update["budget_usage"] = usage
    update["verification_findings"] = findings
    update["current_page_status"] = "budget_exhausted"
    update["last_failure_reason"] = reason
    return update


def initialize_run_context(state: UIRebuildState) -> dict[str, Any]:
    now = time.time()
    usage = dict(state.get("budget_usage", {}))
    usage.setdefault("run_started_at", now)
    for key in ("agent_invocations", "model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens"):
        usage.setdefault(key, 0)
    context = {
        "run_id": state.get("thread_id", "burncloud-graph-engineering-v1"),
        "started_at": usage["run_started_at"],
        "base_branch": state.get("base_branch", "main"),
        "base_commit": state.get("base_commit", ""),
        "agent_branch": state.get("agent_branch", ""),
        "worktree_root": state.get("worktree_root", ""),
        "model_name": state.get("model_name", ""),
        "page_limit": state.get("page_limit", DEFAULT_POLICY.default_page_limit),
    }
    return {"run_context": context, "budget_usage": usage, "invocation_history": list(state.get("invocation_history", []))}


def recovery_node(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {"recovery_result": {"status": "dry_run"}}
    root = state.get("source_repo_root", "")
    if not root:
        return {"recovery_result": {"status": "no_worktree"}}

    history = checkpoint_history(root)
    request = dict(state.get("recovery_request", {}))
    target = str(request.get("target_commit", "")).strip()
    if not target:
        return {
            "page_checkpoint_history": history,
            "recovery_result": {"status": "not_requested", "known_checkpoints": len(history)},
        }
    if not bool(request.get("confirmed", False)):
        return {
            "page_checkpoint_history": history,
            "recovery_result": {"status": "confirmation_required", "target_commit": target},
        }

    result = restore_page_checkpoint(root, target)
    completed: list[str] = []
    retained: list[dict[str, Any]] = []
    for item in history:
        retained.append(item)
        completed.append(item["page_id"])
        if item["commit"] == target:
            break
    return {
        "recovery_result": result,
        "page_checkpoint_history": retained,
        "completed_pages": completed,
        "current_page": None,
        "current_page_status": "recovered",
        "verification_findings": [],
        "review_findings": [],
        "plan_findings": [],
        "fix_round": 0,
    }


def start_page_context(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    now = time.time()
    root = state["source_repo_root"]
    exists = Path(root).exists()
    context = {
        "page_id": page["id"],
        "role": page["role"],
        "route": page["route"],
        "contract_path": page["contract_path"],
        "started_at": now,
        "baseline_commit": head_commit(root) if exists and state.get("execution_mode") == "write" else "",
        "baseline_dirty_files": changed_source_files(root) if exists and state.get("execution_mode") == "write" else [],
        "plan_round": 0,
        "allowed_files": [],
    }
    usage = dict(state.get("budget_usage", {}))
    usage["page_started_at"] = now
    for key in ("agent_invocations", "model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens"):
        usage[f"page_{key}"] = 0
    return {
        "page_context": context,
        "budget_usage": usage,
        "scout_report": {},
        "implementation_plan": {},
        "plan_findings": [],
        "plan_round": 0,
        "builder_report": {},
        "fixer_report": {},
        "verification_findings": [],
        "review_findings": [],
        "last_failure_reason": "",
        "fix_round": 0,
        "current_page_status": "page_context_ready",
    }


def page_scout_node(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    if state.get("execution_mode", "dry_run") == "dry_run":
        report = {
            "status": "COMPLETE",
            "summary": "Dry-run scout: no model invocation.",
            "relevant_files": [],
            "relevant_symbols": [],
            "data_sources": [],
            "backend_gaps": [],
            "constraints": [],
        }
        return {"scout_report": report, "current_page_status": "scouted"}

    report = run_page_scout_agent(
        model_name=state["model_name"],
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
    )
    usage = dict(report.pop("_usage", {}))
    update: dict[str, Any] = {
        "scout_report": report,
        "current_page_status": "scouted" if report["status"] == "COMPLETE" else "scout_blocked",
    }
    update.update(accumulate_usage(state, usage))
    page_context = dict(state.get("page_context", {}))
    page_context["scout_report"] = report
    update["page_context"] = page_context
    return apply_budget_guard(state, update)


def planner_node(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    round_no = int(state.get("plan_round", 0)) + 1
    if state.get("execution_mode", "dry_run") == "dry_run":
        plan = {
            "status": "COMPLETE",
            "summary": "Dry-run plan: no writes.",
            "allowed_files": [],
            "steps": [],
            "backend_gaps": [],
            "risks": [],
        }
        return {"implementation_plan": plan, "plan_round": round_no, "current_page_status": "planned"}

    report = run_planner_agent(
        model_name=state["model_name"],
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
        scout_report=state.get("scout_report", {}),
        previous_plan_findings=list(state.get("plan_findings", [])),
    )
    usage = dict(report.pop("_usage", {}))
    update: dict[str, Any] = {
        "implementation_plan": report,
        "plan_round": round_no,
        "current_page_status": "planned" if report["status"] == "COMPLETE" else "plan_blocked",
    }
    update.update(accumulate_usage(state, usage))
    page_context = dict(state.get("page_context", {}))
    page_context["implementation_plan"] = report
    page_context["plan_round"] = round_no
    page_context["allowed_files"] = list(report.get("allowed_files", []))
    update["page_context"] = page_context
    return apply_budget_guard(state, update)


def plan_guard_node(state: UIRebuildState) -> dict[str, Any]:
    plan = dict(state.get("implementation_plan", {}))
    findings: list[Finding] = []
    raw_allowed = [str(path) for path in plan.get("allowed_files", [])]
    allowed: list[str] = []

    if plan.get("status") != "COMPLETE":
        findings.append(Finding(
            severity="blocker",
            code="PLAN_NOT_COMPLETE",
            message="Planner did not produce a complete implementation plan.",
            evidence=str(plan.get("summary", "")),
        ))

    for raw_path in raw_allowed:
        raw = raw_path.replace("\\", "/").strip()
        path_obj = Path(raw)
        normalized = normalize_repo_path(raw)
        if path_obj.is_absolute() or any(part in {"..", ".git"} for part in path_obj.parts):
            findings.append(Finding(
                severity="blocker",
                code="PLAN_UNSAFE_PATH",
                message=f"Unsafe planned path: {raw_path}",
            ))
            continue
        allowed.append(normalized)

    allowed = list(dict.fromkeys(allowed))
    if len(allowed) > DEFAULT_POLICY.max_plan_files:
        findings.append(Finding(
            severity="blocker",
            code="PLAN_FILE_BUDGET",
            message=f"Plan contains {len(allowed)} files; maximum is {DEFAULT_POLICY.max_plan_files}.",
            expected="Reduce the page to the smallest correct client-side change.",
        ))
    for path in allowed:
        if not path_is_page_writable(path):
            findings.append(Finding(
                severity="major",
                code="PLAN_OUTSIDE_UI_SCOPE",
                message=f"Page graph may not write outside approved UI prefixes: {path}",
                expected=f"Writable prefixes: {DEFAULT_POLICY.page_write_prefixes}. Report backend requirements as BackendGap.",
            ))

    step_files: set[str] = set()
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        raw = str(step.get("file", ""))
        raw_path = Path(raw.replace("\\", "/").strip())
        if raw_path.is_absolute() or any(part in {"..", ".git"} for part in raw_path.parts):
            findings.append(Finding(
                severity="blocker",
                code="PLAN_STEP_UNSAFE_PATH",
                message=f"Unsafe plan-step path: {raw}",
            ))
            continue
        normalized = normalize_repo_path(raw)
        if normalized:
            step_files.add(normalized)

    unapproved_steps = sorted(path for path in step_files if path not in set(allowed))
    if unapproved_steps:
        findings.append(Finding(
            severity="blocker",
            code="PLAN_STEP_NOT_ALLOWLISTED",
            message=f"Plan steps reference files not listed in allowed_files: {unapproved_steps}",
        ))

    status = "plan_approved" if not findings else "plan_rejected"
    plan["allowed_files"] = allowed
    page_context = dict(state.get("page_context", {}))
    page_context["implementation_plan"] = plan
    page_context["allowed_files"] = allowed
    return {
        "implementation_plan": plan,
        "page_context": page_context,
        "plan_findings": findings,
        "current_page_status": status,
    }


def planned_builder_node(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    if state.get("execution_mode", "dry_run") == "dry_run":
        return {
            "builder_report": {"status": "COMPLETE", "summary": "Dry-run builder: no writes."},
            "current_page_status": "built",
        }

    report = run_planned_builder_agent(
        model_name=state["model_name"],
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        agent_branch=state["agent_branch"],
        page=page,
        scout_report=state.get("scout_report", {}),
        implementation_plan=state.get("implementation_plan", {}),
    )
    usage = dict(report.pop("_usage", {}))
    update: dict[str, Any] = {
        "builder_report": report,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "built" if report["status"] == "COMPLETE" else "builder_blocked",
    }
    update.update(accumulate_usage(state, usage))
    return apply_budget_guard(state, update)


def scope_guard_node(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {"changed_files": [], "verification_findings": [], "current_page_status": "scope_passed"}

    allowed = {
        normalize_repo_path(str(path))
        for path in state.get("implementation_plan", {}).get("allowed_files", [])
    }
    changed = changed_source_files(state["source_repo_root"])
    findings = list(state.get("verification_findings", []))

    unexpected = sorted(path for path in changed if path not in allowed)
    if unexpected:
        findings.append(Finding(
            severity="blocker",
            code="SCOPE_GUARD_UNPLANNED_FILES",
            message=f"Builder changed files outside the approved plan: {unexpected}",
            evidence=f"allowed_files={sorted(allowed)}",
            expected="Restore unrelated files or re-plan explicitly before editing them.",
        ))
    if len(changed) > DEFAULT_POLICY.max_write_files_per_agent:
        findings.append(Finding(
            severity="blocker",
            code="SCOPE_GUARD_FILE_BUDGET",
            message=f"Page diff touches {len(changed)} files; maximum is {DEFAULT_POLICY.max_write_files_per_agent}.",
        ))
    for path in changed:
        if not path_is_page_writable(path):
            findings.append(Finding(
                severity="blocker",
                code="SCOPE_GUARD_PROTECTED_DOMAIN",
                message=f"Page Builder changed a protected non-client path: {path}",
                expected="Backend work must be escalated as a separate capability task.",
            ))

    return {
        "changed_files": changed,
        "verification_findings": findings,
        "current_page_status": "scope_failed" if findings else "scope_passed",
    }
