from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .config import workbench_root as default_workbench_root
from .policy import DEFAULT_POLICY
from .state import UIRebuildState


TASK_SCHEMA_VERSION = 1
_MAX_STRING = 4_000
_MAX_LIST = 64


def runtime_root(workbench_root: str | Path | None = None) -> Path:
    root = Path(workbench_root).resolve() if workbench_root else default_workbench_root()
    return root / "graphs" / "ui-rebuild" / ".runtime"


def _task_key(branch: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "__", branch.strip())
    return value or "unknown-task"


def task_path(branch: str, workbench_root: str | Path | None = None) -> Path:
    return runtime_root(workbench_root) / "tasks" / f"{_task_key(branch)}.json"


def _compact(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[compacted-depth]"
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "... [compacted]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:_MAX_LIST]]
    if isinstance(value, dict):
        return {str(key): _compact(item, depth=depth + 1) for key, item in list(value.items())[:_MAX_LIST]}
    return _compact(str(value), depth=depth + 1)


def projected_task_tokens(state: UIRebuildState) -> int:
    before = int(state.get("task_tokens_before_run", 0) or 0)
    current = int(state.get("budget_usage", {}).get("total_tokens", 0) or 0)
    return before + current


def continuation_allowed(state: UIRebuildState) -> bool:
    if projected_task_tokens(state) >= DEFAULT_POLICY.max_task_tokens:
        return False
    return int(state.get("continuation_runs", 0) or 0) < DEFAULT_POLICY.max_continuation_runs


def _resume_stage(snapshot: dict[str, Any]) -> str:
    if not snapshot.get("current_page"):
        return "fresh"
    status = str(snapshot.get("current_page_status", ""))
    plan = snapshot.get("implementation_plan", {}) or {}
    scout = snapshot.get("scout_report", {}) or {}
    builder = snapshot.get("builder_report", {}) or {}
    safe_node = str(snapshot.get("safe_node", ""))

    if status in {"scouted", "plan_blocked", "plan_rejected", "replan_requested"}:
        return "plan"
    if plan.get("status") == "COMPLETE" and plan.get("allowed_files"):
        # If a Run ended immediately after planning, the old Builder report may be
        # from a previous plan round. Re-enter through Plan Guard/Builder instead of
        # assuming the revised plan was implemented.
        if safe_node in {"修改计划", "计划守卫"}:
            return "build"
        if builder.get("status") == "COMPLETE":
            return "validate"
        return "build"
    if scout.get("status") == "COMPLETE":
        return "plan"
    return "fresh"


def build_task_snapshot(state: UIRebuildState, *, safe_node: str) -> dict[str, Any]:
    payload = {
        "schema_version": TASK_SCHEMA_VERSION,
        "updated_at": time.time(),
        "safe_node": safe_node,
        "agent_branch": state.get("agent_branch", ""),
        "base_commit": state.get("base_commit", ""),
        "branch_task_status": state.get("branch_task_status", "active"),
        "current_page": state.get("current_page"),
        "current_page_status": state.get("current_page_status", ""),
        "completed_pages": list(state.get("completed_pages", [])),
        "page_context": dict(state.get("page_context", {})),
        "scout_report": state.get("scout_report", {}),
        "implementation_plan": state.get("implementation_plan", {}),
        "plan_findings": list(state.get("plan_findings", [])),
        "plan_round": int(state.get("plan_round", 0) or 0),
        "builder_report": state.get("builder_report", {}),
        "verification_findings": list(state.get("verification_findings", [])),
        "review_findings": list(state.get("review_findings", [])),
        "last_verification_findings": list(state.get("last_verification_findings", [])),
        "last_review_findings": list(state.get("last_review_findings", [])),
        "last_failure_reason": state.get("last_failure_reason", ""),
        "fix_round": int(state.get("fix_round", 0) or 0),
        "changed_files": list(state.get("changed_files", [])),
        "page_changed_files": list(state.get("page_changed_files", [])),
        "page_checkpoint_files": list(state.get("page_checkpoint_files", [])),
        "page_checkpoint": state.get("page_checkpoint", {}),
        "page_checkpoint_history": list(state.get("page_checkpoint_history", [])),
        "task_total_tokens": projected_task_tokens(state),
        "continuation_runs": int(state.get("continuation_runs", 0) or 0),
    }
    return _compact(payload)


def _checkpoint_ready(state: UIRebuildState) -> bool:
    if state.get("current_page"):
        return True
    marker = state.get("task_snapshot", {}) or {}
    return str(marker.get("status", "")) in {"restored", "saved"}


def save_task_snapshot(state: UIRebuildState, *, safe_node: str) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {"status": "skipped_dry_run"}
    branch = str(state.get("agent_branch", "")).strip()
    if not branch.startswith("agent/ui-rebuild/"):
        return {"status": "skipped_no_agent_branch"}
    if not _checkpoint_ready(state):
        return {"status": "skipped_before_task_restore"}

    path = task_path(branch, state.get("workbench_root"))
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_task_snapshot(state, safe_node=safe_node)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return {"status": "saved", "path": str(path), "safe_node": safe_node, "task_total_tokens": snapshot["task_total_tokens"]}


def load_task_snapshot(branch: str, workbench_root: str | Path | None = None) -> dict[str, Any] | None:
    path = task_path(branch, workbench_root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != TASK_SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported task snapshot schema at {path}")
    if payload.get("agent_branch") != branch:
        raise RuntimeError(f"Task snapshot branch mismatch at {path}")
    return payload


def restore_task_snapshot_node(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {"task_snapshot": {"status": "skipped_dry_run"}, "resume_page_stage": "fresh"}
    branch = str(state.get("agent_branch", ""))
    snapshot = load_task_snapshot(branch, state.get("workbench_root"))
    if not snapshot:
        return {
            "task_snapshot": {"status": "not_found"},
            "task_tokens_before_run": 0,
            "task_total_tokens": 0,
            "continuation_runs": 0,
            "resume_page_stage": "fresh",
        }

    prior_total = int(snapshot.get("task_total_tokens", 0) or 0)
    continuation_runs = int(snapshot.get("continuation_runs", 0) or 0) + 1
    update: dict[str, Any] = {
        "task_snapshot": {
            "status": "restored",
            "safe_node": snapshot.get("safe_node", ""),
            "path": str(task_path(branch, state.get("workbench_root"))),
        },
        "task_tokens_before_run": prior_total,
        "task_total_tokens": prior_total,
        "continuation_runs": continuation_runs,
        "resume_page_stage": _resume_stage(snapshot),
        "budget_usage": {},
        "invocation_history": [],
        "completed_pages": list(snapshot.get("completed_pages", state.get("completed_pages", []))),
        "current_page": snapshot.get("current_page"),
        "current_page_status": str(snapshot.get("current_page_status", "")),
        "page_context": dict(snapshot.get("page_context", {})),
        "scout_report": dict(snapshot.get("scout_report", {})),
        "implementation_plan": dict(snapshot.get("implementation_plan", {})),
        "plan_findings": list(snapshot.get("plan_findings", [])),
        "plan_round": int(snapshot.get("plan_round", 0) or 0),
        "builder_report": dict(snapshot.get("builder_report", {})),
        "last_verification_findings": list(snapshot.get("last_verification_findings", [])),
        "last_review_findings": list(snapshot.get("last_review_findings", [])),
        "last_failure_reason": str(snapshot.get("last_failure_reason", "")),
        "fix_round": int(snapshot.get("fix_round", 0) or 0),
        "changed_files": list(snapshot.get("changed_files", [])),
        "page_changed_files": list(snapshot.get("page_changed_files", [])),
        "page_checkpoint_files": list(snapshot.get("page_checkpoint_files", [])),
        "page_checkpoint": dict(snapshot.get("page_checkpoint", {})),
        "page_checkpoint_history": list(snapshot.get("page_checkpoint_history", [])),
        "verification_findings": [],
        "review_findings": [],
        "validation_results": [],
    }
    return update


def continuation_checkpoint_node(state: UIRebuildState) -> dict[str, Any]:
    projected = projected_task_tokens(state)
    update: dict[str, Any] = {
        "task_total_tokens": projected,
        "branch_task_status": "active",
        "release_status": "continuation_required",
        "phase": "continuation_required",
    }
    merged = dict(state)
    merged.update(update)
    result = save_task_snapshot(merged, safe_node="自动续跑")
    update["task_snapshot"] = result
    return update
