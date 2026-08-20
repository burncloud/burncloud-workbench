from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from burncloud_ui_rebuild.config import DEFAULT_EXECUTION_MODE, DEFAULT_MODEL_NAME, source_root, workbench_root
from burncloud_ui_rebuild.engineering_nodes import initialize_run_context, recovery_node
from burncloud_ui_rebuild.final_checks import final_quality_check
from burncloud_ui_rebuild.nodes import (
    architecture_agent,
    bootstrap,
    mark_page_complete,
    permission_guardian,
    prepare_worktree,
    repo_scout,
    select_next_page,
    spec_agent,
    write_preflight,
)
from burncloud_ui_rebuild.notifications import error_notifying_node, human_review_notification, recovery_review_notification
from burncloud_ui_rebuild.page_graph import build_page_graph
from burncloud_ui_rebuild.policy import DEFAULT_POLICY, blocking_findings
from burncloud_ui_rebuild.quality_nodes import human_review_gate, page_checkpoint
from burncloud_ui_rebuild.recovery_gate import recovery_confirmation_gate
from burncloud_ui_rebuild.release import publish_pull_request_node, pull_request_completion_notification, release_preflight_node
from burncloud_ui_rebuild.state import UIRebuildState
from burncloud_ui_rebuild.task_store import continuation_allowed, continuation_checkpoint_node, restore_task_snapshot_node


NODE_DEFAULT_MODE = "默认执行模式"
NODE_BOOTSTRAP = "初始化"
NODE_SPEC = "读取规范"
NODE_SCOUT = "仓库侦察"
NODE_PERMISSION = "权限守卫"
NODE_WORKTREE = "准备开发分支"
NODE_RELEASE_PREFLIGHT = "Pull Request 发布预检"
NODE_PREFLIGHT = "写入预检"
NODE_TASK_RESTORE = "恢复任务状态"
NODE_RUN_CONTEXT = "运行上下文"
NODE_RECOVERY_NOTIFY = "恢复通知"
NODE_RECOVERY_GATE = "恢复审批"
NODE_RECOVERY = "恢复检查"
NODE_ARCHITECTURE = "架构规划"
NODE_SELECT_PAGE = "选择下一页"
NODE_PAGE_REBUILD = "页面工程"
NODE_PAGE_CHECKPOINT = "页面检查点"
NODE_MARK_COMPLETE = "标记页面完成"
NODE_CONTINUATION = "保存并自动续跑"
NODE_FINAL_PERMISSION = "最终质量检查"
NODE_AUTO_APPROVE = "自动批准"
NODE_HUMAN_NOTIFY = "人工审核通知"
NODE_HUMAN_GATE = "人工审批"
NODE_RELEASE = "提交 Pull Request"
NODE_COMPLETION_NOTIFY = "完成通知"


def default_execution_mode(state: UIRebuildState) -> dict[str, object]:
    return {
        "execution_mode": state.get("execution_mode", DEFAULT_EXECUTION_MODE),
        "page_limit": state.get("page_limit", DEFAULT_POLICY.default_page_limit),
        "max_fix_rounds": state.get("max_fix_rounds", DEFAULT_POLICY.max_fix_rounds),
        "start_new_task": bool(state.get("start_new_task", False)),
        "autopilot_mode": bool(state.get("autopilot_mode", False)),
    }


def _branch_router(state: UIRebuildState) -> str:
    if state.get("branch_task_status") in {"completed_unintegrated", "awaiting_pr_merge"}:
        return "提交PR"
    return "继续工程"


def _after_recovery(state: UIRebuildState) -> str:
    if state.get("current_page") and state.get("resume_page_stage") in {"plan", "build", "validate"}:
        return "恢复页面"
    return "正常规划"


def _page_router(state: UIRebuildState) -> str:
    return "最终检查" if state.get("current_page") is None else "工程页面"


def _after_page_rebuild(state: UIRebuildState) -> str:
    status = state.get("current_page_status")
    if status == "budget_exhausted" and continuation_allowed(state):
        return "自动续跑"
    blocked_statuses = {
        "scout_blocked",
        "plan_blocked",
        "plan_rejected",
        "builder_blocked",
        "budget_exhausted",
        "fix_exhausted",
        "fix_blocked",
    }
    return "人工介入" if status in blocked_statuses else "页面通过"


def _final_gate_router(state: UIRebuildState) -> str:
    if state.get("autopilot_mode") and not blocking_findings(state.get("final_findings", [])):
        return "自动批准"
    return "人工审核"


def _auto_approve(state: UIRebuildState) -> dict[str, object]:
    return {"human_decision": True, "phase": "auto_approved"}


def _human_router(state: UIRebuildState) -> str:
    return "发布" if state.get("human_decision") else "结束"


def _add_safe_node(builder: StateGraph, name: str, node) -> None:
    builder.add_node(name, error_notifying_node(name, node))


def build_graph(checkpointer=None):
    page_rebuild = build_page_graph()
    builder = StateGraph(UIRebuildState)

    _add_safe_node(builder, NODE_DEFAULT_MODE, default_execution_mode)
    _add_safe_node(builder, NODE_BOOTSTRAP, bootstrap)
    _add_safe_node(builder, NODE_SPEC, spec_agent)
    _add_safe_node(builder, NODE_SCOUT, repo_scout)
    _add_safe_node(builder, NODE_PERMISSION, permission_guardian)
    _add_safe_node(builder, NODE_WORKTREE, prepare_worktree)
    _add_safe_node(builder, NODE_RELEASE_PREFLIGHT, release_preflight_node)
    _add_safe_node(builder, NODE_PREFLIGHT, write_preflight)
    _add_safe_node(builder, NODE_TASK_RESTORE, restore_task_snapshot_node)
    _add_safe_node(builder, NODE_RUN_CONTEXT, initialize_run_context)
    builder.add_node(NODE_RECOVERY_NOTIFY, recovery_review_notification)
    builder.add_node(NODE_RECOVERY_GATE, recovery_confirmation_gate)
    _add_safe_node(builder, NODE_RECOVERY, recovery_node)
    _add_safe_node(builder, NODE_ARCHITECTURE, architecture_agent)
    _add_safe_node(builder, NODE_SELECT_PAGE, select_next_page)
    builder.add_node(NODE_PAGE_REBUILD, page_rebuild)
    _add_safe_node(builder, NODE_PAGE_CHECKPOINT, page_checkpoint)
    _add_safe_node(builder, NODE_MARK_COMPLETE, mark_page_complete)
    _add_safe_node(builder, NODE_CONTINUATION, continuation_checkpoint_node)
    _add_safe_node(builder, NODE_FINAL_PERMISSION, final_quality_check)
    _add_safe_node(builder, NODE_AUTO_APPROVE, _auto_approve)
    builder.add_node(NODE_HUMAN_NOTIFY, human_review_notification)
    builder.add_node(NODE_HUMAN_GATE, human_review_gate)
    _add_safe_node(builder, NODE_RELEASE, publish_pull_request_node)
    builder.add_node(NODE_COMPLETION_NOTIFY, pull_request_completion_notification)

    builder.add_edge(START, NODE_DEFAULT_MODE)
    builder.add_edge(NODE_DEFAULT_MODE, NODE_BOOTSTRAP)
    builder.add_edge(NODE_BOOTSTRAP, NODE_SPEC)
    builder.add_edge(NODE_SPEC, NODE_SCOUT)
    builder.add_edge(NODE_SCOUT, NODE_PERMISSION)
    builder.add_edge(NODE_PERMISSION, NODE_WORKTREE)
    builder.add_conditional_edges(NODE_WORKTREE, _branch_router, {"继续工程": NODE_RELEASE_PREFLIGHT, "提交PR": NODE_RELEASE})
    builder.add_edge(NODE_RELEASE_PREFLIGHT, NODE_PREFLIGHT)
    builder.add_edge(NODE_PREFLIGHT, NODE_TASK_RESTORE)
    builder.add_edge(NODE_TASK_RESTORE, NODE_RUN_CONTEXT)
    builder.add_edge(NODE_RUN_CONTEXT, NODE_RECOVERY_NOTIFY)
    builder.add_edge(NODE_RECOVERY_NOTIFY, NODE_RECOVERY_GATE)
    builder.add_edge(NODE_RECOVERY_GATE, NODE_RECOVERY)
    builder.add_conditional_edges(NODE_RECOVERY, _after_recovery, {"恢复页面": NODE_PAGE_REBUILD, "正常规划": NODE_ARCHITECTURE})
    builder.add_edge(NODE_ARCHITECTURE, NODE_SELECT_PAGE)

    builder.add_conditional_edges(NODE_SELECT_PAGE, _page_router, {"工程页面": NODE_PAGE_REBUILD, "最终检查": NODE_FINAL_PERMISSION})
    builder.add_conditional_edges(
        NODE_PAGE_REBUILD,
        _after_page_rebuild,
        {"页面通过": NODE_PAGE_CHECKPOINT, "自动续跑": NODE_CONTINUATION, "人工介入": NODE_FINAL_PERMISSION},
    )
    builder.add_edge(NODE_CONTINUATION, END)
    builder.add_edge(NODE_PAGE_CHECKPOINT, NODE_MARK_COMPLETE)
    builder.add_edge(NODE_MARK_COMPLETE, NODE_SELECT_PAGE)
    builder.add_conditional_edges(
        NODE_FINAL_PERMISSION,
        _final_gate_router,
        {"自动批准": NODE_AUTO_APPROVE, "人工审核": NODE_HUMAN_NOTIFY},
    )
    builder.add_edge(NODE_AUTO_APPROVE, NODE_RELEASE)
    builder.add_edge(NODE_HUMAN_NOTIFY, NODE_HUMAN_GATE)
    builder.add_conditional_edges(NODE_HUMAN_GATE, _human_router, {"发布": NODE_RELEASE, "结束": END})
    builder.add_edge(NODE_RELEASE, NODE_COMPLETION_NOTIFY)
    builder.add_edge(NODE_COMPLETION_NOTIFY, END)
    return builder.compile(checkpointer=checkpointer)


def initial_state(
    *,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    thread_id: str = "burncloud-graph-engineering-v1",
    model_name: str = DEFAULT_MODEL_NAME,
    page_limit: int | None = DEFAULT_POLICY.default_page_limit,
    recovery_target_commit: str = "",
    recovery_confirmed: bool = False,
    start_new_task: bool = False,
    autopilot_mode: bool = False,
) -> UIRebuildState:
    base_repo = str(source_root())
    state: UIRebuildState = {
        "thread_id": thread_id,
        "execution_mode": execution_mode,  # type: ignore[typeddict-item]
        "model_name": model_name or DEFAULT_MODEL_NAME,
        "base_repo_root": base_repo,
        "base_branch": "main",
        "source_repo_root": base_repo,
        "workbench_root": str(workbench_root()),
        "max_fix_rounds": DEFAULT_POLICY.max_fix_rounds,
        "start_new_task": start_new_task,
        "autopilot_mode": autopilot_mode,
        "completed_pages": [],
        "implementation_results": [],
        "page_checkpoint_history": [],
        "invocation_history": [],
        "notification_history": [],
        "budget_usage": {},
        "run_context": {},
        "page_context": {},
        "task_snapshot": {},
        "task_tokens_before_run": 0,
        "task_total_tokens": 0,
        "continuation_runs": 0,
        "resume_page_stage": "fresh",
        "release_preflight": {},
        "warnings": [],
        "phase": "start",
    }
    if page_limit is not None:
        state["page_limit"] = page_limit
    if recovery_target_commit:
        state["recovery_request"] = {"target_commit": recovery_target_commit, "confirmed": recovery_confirmed}
    return state


# Agent Server / Studio injects persistence at runtime.
graph = build_graph()
