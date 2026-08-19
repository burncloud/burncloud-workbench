from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from .agents import run_builder_agent, run_fixer_agent, run_reviewer_agent
from .coding_tools import changed_source_files, git_status, run_named_validation
from .config import DEFAULT_MODEL_NAME, source_root as default_source_root, workbench_root as default_workbench_root
from .manifest import TARGET_PAGES
from .permissions import validate_target_manifest
from .state import Finding, UIRebuildState


REQUIRED_SPEC_PATHS = (
    "docs/ui/product-standard.md",
    "docs/ui/information-architecture.md",
    "docs/ui/design-system.md",
    "docs/ui/interaction-rules.md",
    "docs/ui/content-standard.md",
    "docs/ui/review-checklist.md",
    "docs/ui/agent-execution.md",
)

SCOUT_PATHS = (
    "crates/client/src/app.rs",
    "crates/client/src/auth_gate.rs",
    "crates/client/src/backend.rs",
    "crates/client/src/lib.rs",
    "crates/server/src/api/mod.rs",
    "crates/server/src/api/auth.rs",
    "crates/database/crates/user/src/lib.rs",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_model(state: UIRebuildState) -> str:
    return state.get("model_name", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME


def bootstrap(state: UIRebuildState) -> dict[str, Any]:
    """Populate deterministic defaults so Studio runs can omit routine settings."""
    return {
        "thread_id": state.get("thread_id", "burncloud-ui-rebuild-studio"),
        "execution_mode": state.get("execution_mode", "dry_run"),
        "model_name": state.get("model_name", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME,
        "source_repo_root": state.get("source_repo_root") or str(default_source_root()),
        "workbench_root": state.get("workbench_root") or str(default_workbench_root()),
        "max_fix_rounds": state.get("max_fix_rounds", 3),
        "completed_pages": list(state.get("completed_pages", [])),
        "implementation_results": list(state.get("implementation_results", [])),
        "warnings": list(state.get("warnings", [])),
        "phase": "bootstrapped",
    }


def spec_agent(state: UIRebuildState) -> dict[str, Any]:
    """Role 2 — Rules keeper: load target truth, never infer it from old UI."""
    root = Path(state["workbench_root"])
    specs: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    queue = list(TARGET_PAGES)
    page_limit = state.get("page_limit")
    if page_limit is not None:
        if page_limit < 1 or page_limit > len(TARGET_PAGES):
            raise ValueError(f"page_limit must be between 1 and {len(TARGET_PAGES)}")
        queue = queue[:page_limit]

    paths = [*REQUIRED_SPEC_PATHS, *(task["contract_path"] for task in queue)]
    for relative in paths:
        path = root / relative
        if not path.exists():
            missing.append(relative)
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        first_heading = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
        specs[relative] = {"sha256": _sha256(path), "title": first_heading}

    if missing:
        raise FileNotFoundError(f"Missing approved UI specifications: {missing}")

    return {"specs": specs, "page_queue": queue, "phase": "specs_loaded"}


def repo_scout(state: UIRebuildState) -> dict[str, Any]:
    """Role 3 — Scout: inspect current source truth; never modify it."""
    root = Path(state["source_repo_root"])
    evidence: dict[str, Any] = {}
    routes: list[str] = []
    warnings = list(state.get("warnings", []))

    if not root.exists():
        warnings.append(f"BurnCloud source repo not found at {root}; repo scouting is partial.")
        return {"repo_evidence": evidence, "current_routes": routes, "warnings": warnings, "phase": "repo_scout_partial"}

    route_pattern = re.compile(r'#\[route\("([^"]+)"\)\]')
    for relative in SCOUT_PATHS:
        path = root / relative
        if not path.exists():
            evidence[relative] = {"exists": False}
            continue
        text = path.read_text(encoding="utf-8")
        evidence[relative] = {
            "exists": True,
            "sha256": _sha256(path),
            "contains_console": "/console" in text,
            "contains_roles": "roles" in text or "get_user_roles" in text,
        }
        if relative == "crates/client/src/app.rs":
            routes.extend(route_pattern.findall(text))

    return {"repo_evidence": evidence, "current_routes": routes, "warnings": warnings, "phase": "repo_scanned"}


def permission_guardian(state: UIRebuildState) -> dict[str, Any]:
    """Role 4 — Permission Guardian: deterministic gate with veto power."""
    findings = validate_target_manifest(state.get("page_queue", TARGET_PAGES))
    current_routes = state.get("current_routes", [])
    current_management = [r for r in current_routes if r not in {"/", "/home", "/landing", "/login", "/register"}]
    legacy_root_routes = [r for r in current_management if not r.startswith("/console")]

    if legacy_root_routes:
        findings.append(Finding(
            severity="info",
            code="CURRENT_CONSOLE_NAMESPACE_GAP",
            message="Current client still exposes management UI routes outside /console.",
            evidence=", ".join(sorted(legacy_root_routes)),
            expected="/console/*",
        ))

    blocking = [item for item in findings if item["severity"] == "blocker"]
    if blocking:
        raise RuntimeError(f"Permission manifest failed: {blocking}")
    return {"permission_findings": findings, "phase": "permission_checked"}


def write_preflight(state: UIRebuildState) -> dict[str, Any]:
    """Refuse live Agent writes unless the source tree starts clean."""
    if state.get("execution_mode", "dry_run") != "write":
        return {"phase": "write_preflight_skipped"}
    status = git_status(state["source_repo_root"])
    if status:
        raise RuntimeError(
            "Live rebuild requires a clean BurnCloud source working tree before Agent writes. "
            f"Current git status:\n{status}"
        )
    return {"phase": "write_preflight_passed"}


def architecture_agent(state: UIRebuildState) -> dict[str, Any]:
    """Role 5 — Architect: define the new foundation before pages are built."""
    return {
        "architecture_plan": {
            "management_namespace": "/console/*",
            "layouts": {
                "buyer": "BuyerLayout + BuyerRoleGate",
                "supplier": "SupplierLayout + SupplierRoleGate",
                "admin": "AdminLayout + AdminRoleGate",
            },
            "identity": {
                "source_of_truth": "server/database roles",
                "multi_role": True,
                "standard_multi_role_case": ["buyer", "supplier"],
                "workspace_preference": "persist last_workspace; authorization overrides memory",
            },
            "server_boundaries": {
                "management_api": "/console/api/*",
                "internal_control": "/console/internal/*",
                "inference_data_plane": "/v1/*",
            },
        },
        "completed_pages": list(state.get("completed_pages", [])),
        "implementation_results": list(state.get("implementation_results", [])),
        "verification_findings": [],
        "review_findings": [],
        "fix_round": 0,
        "phase": "foundation_ready",
    }


def select_next_page(state: UIRebuildState) -> dict[str, Any]:
    completed = set(state.get("completed_pages", []))
    next_page = next((task for task in state.get("page_queue", TARGET_PAGES) if task["id"] not in completed), None)
    return {
        "current_page": next_page,
        "current_page_status": "selected" if next_page else "all_pages_complete",
        "fix_round": 0,
        "verification_findings": [],
        "review_findings": [],
    }


def builder_agent(state: UIRebuildState) -> dict[str, Any]:
    """Role 6 — Builder: dry-run plans or live create_agent implementation."""
    page = state.get("current_page")
    if page is None:
        return {}

    if state.get("execution_mode", "dry_run") == "dry_run":
        result = {
            "page_id": page["id"],
            "route": page["route"],
            "contract_path": page["contract_path"],
            "status": "dry_run_planned",
            "note": "No source files were modified.",
        }
        return {
            "implementation_results": [*state.get("implementation_results", []), result],
            "current_page_status": "built",
        }

    report = run_builder_agent(
        model_name=_require_model(state),
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
        architecture_plan=state.get("architecture_plan", {}),
        permission_findings=state.get("permission_findings", []),
        allow_write=True,
    )
    result = {
        "page_id": page["id"],
        "route": page["route"],
        "contract_path": page["contract_path"],
        **report,
    }
    return {
        "implementation_results": [*state.get("implementation_results", []), result],
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "built" if report["status"] == "COMPLETE" else "builder_blocked",
    }


def verifier(state: UIRebuildState) -> dict[str, Any]:
    """Role 7 — Verifier: deterministic executable contract checks."""
    page = state.get("current_page")
    findings: list[Finding] = []
    validation_results: list[dict[str, Any]] = []
    if page is None:
        return {"verification_findings": findings, "validation_results": validation_results}

    if not (page["route"] == "/console" or page["route"].startswith("/console/")):
        findings.append(Finding(
            severity="blocker",
            code="CONSOLE_NAMESPACE",
            message="Management page escaped /console namespace.",
            evidence=page["route"],
            expected="/console/*",
        ))

    root = Path(state["workbench_root"])
    if not (root / page["contract_path"]).exists():
        findings.append(Finding(
            severity="blocker",
            code="MISSING_PAGE_CONTRACT",
            message=f'Missing page contract for {page["id"]}.',
            expected=page["contract_path"],
        ))

    if state.get("execution_mode", "dry_run") == "write":
        for name in ("cargo_fmt_check", "client_check"):
            result = run_named_validation(state["source_repo_root"], name)
            validation_results.append(result)
            if result["returncode"] != 0:
                findings.append(Finding(
                    severity="blocker",
                    code=f"VALIDATION_{name.upper()}",
                    message=f"Validation failed: {name}",
                    evidence=str(result["output"]),
                    expected="returncode 0",
                ))

    return {
        "verification_findings": findings,
        "validation_results": validation_results,
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "verified" if not findings else "verification_failed",
    }


def reviewer(state: UIRebuildState) -> dict[str, Any]:
    """Role 8 — Reviewer: independent judge; never edits code."""
    page = state.get("current_page")
    if page is None:
        return {"review_findings": []}

    if state.get("execution_mode", "dry_run") == "dry_run":
        findings = list(state.get("verification_findings", []))
        if page["role"] not in {"buyer", "supplier", "admin"}:
            findings.append(Finding(
                severity="blocker",
                code="UNKNOWN_WORKSPACE_ROLE",
                message=f'Unknown page role: {page["role"]}',
            ))
        return {
            "review_findings": findings,
            "current_page_status": "review_passed" if not findings else "review_failed",
        }

    report = run_reviewer_agent(
        model_name=_require_model(state),
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
        architecture_plan=state.get("architecture_plan", {}),
        verification_findings=state.get("verification_findings", []),
        changed_files=state.get("changed_files", []),
    )
    findings = [Finding(**item) for item in report["findings"]]
    if report["decision"] == "FAIL" and not findings:
        findings.append(Finding(
            severity="major",
            code="REVIEW_FAIL_WITHOUT_FINDING",
            message=report["summary"],
        ))
    return {
        "review_findings": findings,
        "review_summary": report["summary"],
        "current_page_status": "review_passed" if report["decision"] == "PASS" and not findings else "review_failed",
    }


def fixer(state: UIRebuildState) -> dict[str, Any]:
    """Role 9 — Fixer: bounded correction only; no unrelated refactors."""
    current = state.get("fix_round", 0) + 1
    max_rounds = state.get("max_fix_rounds", 3)
    if current > max_rounds:
        page = state.get("current_page")
        page_id = page["id"] if page else "unknown"
        raise RuntimeError(f"Fix loop exceeded max rounds ({max_rounds}) for {page_id}.")

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
        model_name=_require_model(state),
        source_root=state["source_repo_root"],
        workbench_root=state["workbench_root"],
        page=page,
        review_findings=state.get("review_findings", []),
    )
    return {
        "fix_round": current,
        "verification_findings": [],
        "review_findings": [],
        "changed_files": changed_source_files(state["source_repo_root"]),
        "current_page_status": "fix_applied" if report["status"] == "COMPLETE" else "fix_blocked",
    }


def mark_page_complete(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    completed = list(state.get("completed_pages", []))
    if page["id"] not in completed:
        completed.append(page["id"])
    return {"completed_pages": completed, "current_page": None, "current_page_status": "complete"}


def final_permission_check(state: UIRebuildState) -> dict[str, Any]:
    findings = validate_target_manifest(state.get("page_queue", TARGET_PAGES))
    completed = set(state.get("completed_pages", []))
    expected = {task["id"] for task in state.get("page_queue", TARGET_PAGES)}
    missing = sorted(expected - completed)
    if missing:
        findings.append(Finding(
            severity="blocker",
            code="INCOMPLETE_PAGE_QUEUE",
            message=f"Not all target pages completed: {missing}",
        ))
    return {"final_findings": findings, "phase": "final_permission_checked"}


def human_gate(state: UIRebuildState) -> dict[str, Any]:
    decision = interrupt({
        "type": "burncloud_ui_rebuild_final_gate",
        "execution_mode": state.get("execution_mode", "dry_run"),
        "completed_pages": len(state.get("completed_pages", [])),
        "total_pages": len(state.get("page_queue", TARGET_PAGES)),
        "changed_files": state.get("changed_files", []),
        "validation_results": state.get("validation_results", []),
        "final_findings": state.get("final_findings", []),
        "question": "Approve this UI rebuild run for release processing?",
    })
    return {"human_decision": bool(decision)}


def release_agent(state: UIRebuildState) -> dict[str, Any]:
    """Role 10 — Release Agent: release only approved work."""
    if not state.get("human_decision"):
        return {"release_status": "rejected", "phase": "done"}
    if state.get("execution_mode", "dry_run") == "dry_run":
        return {"release_status": "dry_run_complete_no_git_write", "phase": "done"}
    return {
        "release_status": "approved_local_changes_no_git_publish",
        "phase": "done",
    }
