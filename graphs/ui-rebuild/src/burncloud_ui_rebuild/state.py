from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


WorkspaceRole = Literal["buyer", "supplier", "admin"]
ExecutionMode = Literal["dry_run", "write"]


class PageTask(TypedDict):
    id: str
    role: WorkspaceRole
    page: str
    route: str
    contract_path: str
    phase: str


class Finding(TypedDict):
    severity: Literal["blocker", "major", "minor", "info"]
    code: str
    message: str
    evidence: NotRequired[str]
    expected: NotRequired[str]


class UIRebuildState(TypedDict, total=False):
    thread_id: str
    execution_mode: ExecutionMode
    model_name: str
    page_limit: int

    base_repo_root: str
    base_branch: str
    base_commit: str
    agent_branch: str
    worktree_root: str
    worktree_reused: bool
    source_repo_root: str
    workbench_root: str
    max_fix_rounds: int

    specs: dict[str, dict[str, str]]
    page_queue: list[PageTask]

    repo_evidence: dict[str, Any]
    current_routes: list[str]
    source_baseline_status: str

    permission_findings: list[Finding]
    architecture_plan: dict[str, Any]

    current_page: PageTask | None
    current_page_status: str
    completed_pages: list[str]
    implementation_results: list[dict[str, Any]]
    builder_report: dict[str, Any]
    fixer_report: dict[str, Any]
    changed_files: list[str]
    validation_results: list[dict[str, Any]]
    verification_findings: list[Finding]
    review_findings: list[Finding]
    fix_round: int

    final_findings: list[Finding]
    human_decision: bool
    release_status: str
    phase: str
    warnings: list[str]
