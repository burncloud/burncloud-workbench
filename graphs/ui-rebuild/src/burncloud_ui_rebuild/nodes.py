from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .config import DEFAULT_MODEL_NAME, source_root as default_source_root, workbench_root as default_workbench_root
from .manifest import TARGET_PAGES
from .permissions import validate_target_manifest
from .state import Finding, UIRebuildState
from .worktree import current_branch, porcelain_status, prepare_agent_worktree


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


def bootstrap(state: UIRebuildState) -> dict[str, Any]:
    """Populate deterministic defaults before any repository or Agent work."""
    base_repo = state.get("base_repo_root") or state.get("source_repo_root") or str(default_source_root())
    return {
        "thread_id": state.get("thread_id", "burncloud-graph-engineering-v1"),
        "execution_mode": state.get("execution_mode", "dry_run"),
        "model_name": state.get("model_name", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME,
        "base_repo_root": base_repo,
        "base_branch": state.get("base_branch", "main"),
        "source_repo_root": state.get("worktree_root") or base_repo,
        "workbench_root": state.get("workbench_root") or str(default_workbench_root()),
        "max_fix_rounds": state.get("max_fix_rounds", 3),
        "completed_pages": list(state.get("completed_pages", [])),
        "implementation_results": list(state.get("implementation_results", [])),
        "warnings": list(state.get("warnings", [])),
        "phase": "bootstrapped",
    }


def spec_agent(state: UIRebuildState) -> dict[str, Any]:
    """Load approved target truth and the bounded page queue."""
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
    """Deterministically inspect high-value repository facts before page Agents run."""
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
    """Deterministic permission/namespace gate with veto power."""
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


def prepare_worktree(state: UIRebuildState) -> dict[str, Any]:
    """Create once, then reuse the isolated Agent branch/worktree across runs."""
    if state.get("execution_mode", "dry_run") != "write":
        return {"phase": "worktree_skipped"}

    existing_branch = state.get("agent_branch")
    existing_root = state.get("worktree_root")
    if existing_branch and existing_root:
        root = Path(existing_root).resolve()
        if not root.exists():
            raise RuntimeError(f"Recorded Agent worktree no longer exists: {root}")
        actual = current_branch(root)
        if actual != existing_branch:
            raise RuntimeError(f"Recorded Agent worktree branch mismatch: expected {existing_branch!r}, found {actual!r}.")
        return {"source_repo_root": str(root), "worktree_reused": True, "phase": "worktree_reused"}

    prepared = prepare_agent_worktree(state["base_repo_root"], base_branch=state.get("base_branch", "main"))
    return {**prepared, "phase": "worktree_reused" if prepared.get("worktree_reused") else "worktree_prepared"}


def write_preflight(state: UIRebuildState) -> dict[str, Any]:
    """Protect main while allowing a reused Agent worktree to retain in-progress diff."""
    if state.get("execution_mode", "dry_run") != "write":
        return {"phase": "write_preflight_skipped"}

    base = Path(state["base_repo_root"]).resolve()
    source = Path(state["source_repo_root"]).resolve()
    expected_branch = state.get("agent_branch", "")
    if not expected_branch:
        raise RuntimeError("Live rebuild has no Agent branch; prepare_worktree must run first.")
    if source == base:
        raise RuntimeError("Direct writes to the primary BurnCloud checkout are forbidden; an Agent worktree is required.")

    actual_branch = current_branch(source)
    if actual_branch in {"main", "master"}:
        raise RuntimeError(f"Direct writes to protected branch {actual_branch!r} are forbidden.")
    if actual_branch != expected_branch:
        raise RuntimeError(f"Agent worktree branch mismatch: expected {expected_branch!r}, current branch is {actual_branch!r}.")
    if porcelain_status(base):
        raise RuntimeError("Primary BurnCloud checkout is dirty; refusing Agent writes until main is clean.")

    status = porcelain_status(source)
    reused = bool(state.get("worktree_reused", False))
    if status and not reused:
        raise RuntimeError("A newly-created Agent worktree must start clean before Builder writes. " f"Current git status:\n{status}")

    warnings = list(state.get("warnings", []))
    if status and reused:
        warnings.append("Reusing the existing UI rebuild worktree with in-progress Agent changes; this run will continue that diff.")
        baseline_status = "continuing_existing_changes"
    elif reused:
        baseline_status = "reused_clean_worktree"
    else:
        baseline_status = "clean_new_worktree"
    return {"source_baseline_status": baseline_status, "warnings": warnings, "phase": "write_preflight_passed"}


def architecture_agent(state: UIRebuildState) -> dict[str, Any]:
    """Publish stable target architecture facts for the bounded page graph."""
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


def mark_page_complete(state: UIRebuildState) -> dict[str, Any]:
    page = state.get("current_page")
    if page is None:
        return {}
    completed = list(state.get("completed_pages", []))
    if page["id"] not in completed:
        completed.append(page["id"])
    return {"completed_pages": completed, "current_page": None, "current_page_status": "complete"}


def release_agent(state: UIRebuildState) -> dict[str, Any]:
    """Record release eligibility; v1 still never pushes, merges or writes main."""
    if not state.get("human_decision"):
        return {"release_status": "rejected", "phase": "done"}
    blockers = [item for item in state.get("final_findings", []) if item.get("severity") == "blocker"]
    if blockers:
        return {
            "release_status": "blocked_by_final_findings",
            "agent_branch": state.get("agent_branch", ""),
            "worktree_root": state.get("worktree_root", ""),
            "phase": "done",
        }
    if state.get("execution_mode", "dry_run") == "dry_run":
        return {"release_status": "dry_run_complete_no_git_write", "phase": "done"}
    return {
        "release_status": "approved_agent_branch_no_git_publish",
        "agent_branch": state.get("agent_branch", ""),
        "worktree_root": state.get("worktree_root", ""),
        "phase": "done",
    }
