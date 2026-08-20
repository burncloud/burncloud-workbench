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


class InvocationUsage(TypedDict, total=False):
    role: str
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class BudgetUsage(TypedDict, total=False):
    run_started_at: float
    page_started_at: float
    agent_invocations: int
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    page_agent_invocations: int
    page_model_calls: int
    page_tool_calls: int
    page_input_tokens: int
    page_output_tokens: int
    page_total_tokens: int
    exhausted_reason: str


class RunContext(TypedDict, total=False):
    run_id: str
    started_at: float
    base_branch: str
    base_commit: str
    agent_branch: str
    source_repo_root: str
    branch_reused: bool
    model_name: str
    page_limit: int
    task_tokens_before_run: int
    continuation_runs: int


class PageContext(TypedDict, total=False):
    page_id: str
    role: WorkspaceRole
    route: str
    contract_path: str
    started_at: float
    baseline_commit: str
    baseline_dirty_files: list[str]
    baseline_dirty_fingerprints: dict[str, str]
    scout_report: dict[str, Any]
    implementation_plan: dict[str, Any]
    plan_round: int
    allowed_files: list[str]
    checkpoint_commit: str


class RecoveryRequest(TypedDict, total=False):
    target_commit: str
    confirmed: bool


class NotificationEvent(TypedDict, total=False):
    event: str
    status: str
    event_key: str
    http_status: int
    reason: str


class UIRebuildState(TypedDict, total=False):
    # Stable runtime fields.
    thread_id: str
    execution_mode: ExecutionMode
    model_name: str
    page_limit: int
    start_new_task: bool

    base_repo_root: str
    base_branch: str
    base_commit: str
    agent_branch: str
    source_repo_root: str
    branch_reused: bool
    branch_task_status: str
    workbench_root: str
    max_fix_rounds: int

    # Cross-Run Task persistence. One engineering Task can consume multiple
    # bounded Runs while staying on the same Agent branch.
    task_snapshot: dict[str, Any]
    task_tokens_before_run: int
    task_total_tokens: int
    continuation_runs: int
    resume_page_stage: str

    # Temporary compatibility fields for old persisted Studio threads. New runs
    # never create a Git worktree; worktree_root, if present, equals source_repo_root.
    worktree_root: str
    worktree_reused: bool

    # Layered Graph Engineering state.
    run_context: RunContext
    page_context: PageContext
    budget_usage: BudgetUsage
    invocation_history: list[InvocationUsage]
    recovery_request: RecoveryRequest
    recovery_result: dict[str, Any]
    notification_history: list[NotificationEvent]

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

    scout_report: dict[str, Any]
    implementation_plan: dict[str, Any]
    plan_findings: list[Finding]
    plan_round: int
    builder_report: dict[str, Any]
    fixer_report: dict[str, Any]
    review_summary: str

    changed_files: list[str]
    page_changed_files: list[str]
    page_checkpoint_files: list[str]
    validation_results: list[dict[str, Any]]
    reality_report: dict[str, Any]
    verification_findings: list[Finding]
    review_findings: list[Finding]
    last_verification_findings: list[Finding]
    last_review_findings: list[Finding]
    last_failure_reason: str
    fix_round: int

    page_checkpoint: dict[str, Any]
    page_checkpoint_history: list[dict[str, Any]]

    final_findings: list[Finding]
    human_decision: bool
    release_preflight: dict[str, Any]
    release_status: str
    pull_request_number: int
    pull_request_url: str
    pull_request_title: str
    pull_request_status: str
    phase: str
    warnings: list[str]
