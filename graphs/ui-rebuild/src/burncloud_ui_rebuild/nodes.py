from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

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


def spec_agent(state: UIRebuildState) -> dict[str, Any]:
    """Role 2 — Rules keeper: load target truth, never infer it from old UI."""
    root = Path(state["workbench_root"])
    specs: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    paths = [*REQUIRED_SPEC_PATHS, *(task["contract_path"] for task in TARGET_PAGES)]

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

    return {"specs": specs, "page_queue": list(TARGET_PAGES), "phase": "specs_loaded"}


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
    """Role 6 — Builder: v0.1 is intentionally dry-run only."""
    page = state.get("current_page")
    if page is None:
        return {}
    if state.get("execution_mode", "dry_run") != "dry_run":
        raise RuntimeError("v0.1 write mode is disabled until sandboxed edit tools are wired.")

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


def verifier(state: UIRebuildState) -> dict[str, Any]:
    """Role 7 — Verifier: executable contract checks."""
    page = state.get("current_page")
    findings: list[Finding] = []
    if page is None:
        return {"verification_findings": findings}

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

    return {
        "verification_findings": findings,
        "current_page_status": "verified" if not findings else "verification_failed",
    }


def reviewer(state: UIRebuildState) -> dict[str, Any]:
    """Role 8 — Reviewer: independent judge; never edits code."""
    findings = list(state.get("verification_findings", []))
    page = state.get("current_page")
    if page is not None and page["role"] not in {"buyer", "supplier", "admin"}:
        findings.append(Finding(
            severity="blocker",
            code="UNKNOWN_WORKSPACE_ROLE",
            message=f'Unknown page role: {page["role"]}',
        ))
    return {
        "review_findings": findings,
        "current_page_status": "review_passed" if not findings else "review_failed",
    }


def fixer(state: UIRebuildState) -> dict[str, Any]:
    """Role 9 — Fixer: bounded correction only; no unrelated refactors."""
    current = state.get("fix_round", 0) + 1
    max_rounds = state.get("max_fix_rounds", 3)
    if current > max_rounds:
        page = state.get("current_page")
        page_id = page["id"] if page else "unknown"
        raise RuntimeError(f"Fix loop exceeded max rounds ({max_rounds}) for {page_id}.")
    return {
        "fix_round": current,
        "verification_findings": [],
        "review_findings": [],
        "current_page_status": "fix_applied_dry_run",
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
    raise RuntimeError("v0.1 release write tools are disabled until branch-only publishing is wired.")
