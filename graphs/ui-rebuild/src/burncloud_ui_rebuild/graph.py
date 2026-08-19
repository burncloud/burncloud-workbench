from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from burncloud_ui_rebuild.config import DEFAULT_EXECUTION_MODE, DEFAULT_MODEL_NAME, source_root, workbench_root
from burncloud_ui_rebuild.nodes import (
    architecture_agent,
    bootstrap,
    final_permission_check,
    human_gate,
    mark_page_complete,
    permission_guardian,
    prepare_worktree,
    release_agent,
    repo_scout,
    select_next_page,
    spec_agent,
    write_preflight,
)
from burncloud_ui_rebuild.page_graph import build_page_graph
from burncloud_ui_rebuild.policy import DEFAULT_POLICY
from burncloud_ui_rebuild.quality_nodes import page_checkpoint
from burncloud_ui_rebuild.state import UIRebuildState


NODE_DEFAULT_MODE = "默认执行模式"
NODE_BOOTSTRAP = "初始化"
NODE_SPEC = "读取规范"
NODE_SCOUT = "代码侦察"
NODE_PERMISSION = "权限守卫"
NODE_WORKTREE = "创建开发分支"
NODE_PREFLIGHT = "写入预检"
NODE_ARCHITECTURE = "架构规划"
NODE_SELECT_PAGE = "选择下一页"
NODE_PAGE_REBUILD = "页面重建"
NODE_PAGE_CHECKPOINT = "页面检查点"
NODE_MARK_COMPLETE = "标记页面完成"
NODE_FINAL_PERMISSION = "最终权限检查"
NODE_HUMAN_GATE = "人工审批"
NODE_RELEASE = "发布"


def default_execution_mode(state: UIRebuildState) -> dict[str, object]:
    """Default Studio/Agent Server runs to live write mode and one page unless overridden."""
    return {
        "execution_mode": state.get("execution_mode", DEFAULT_EXECUTION_MODE),
        "page_limit": state.get("page_limit", DEFAULT_POLICY.default_page_limit),
        "max_fix_rounds": state.get("max_fix_rounds", DEFAULT_POLICY.max_fix_rounds),
    }


def _page_router(state: UIRebuildState) -> str:
    return "最终检查" if state.get("current_page") is None else "重建页面"


def _after_page_rebuild(state: UIRebuildState) -> str:
    if state.get("current_page_status") in {"fix_exhausted", "fix_blocked", "builder_blocked"}:
        return "人工介入"
    return "页面通过"


def _human_router(state: UIRebuildState) -> str:
    return "发布" if state.get("human_decision") else "结束"


def build_graph(checkpointer=None):
    page_rebuild = build_page_graph()
    builder = StateGraph(UIRebuildState)

    builder.add_node(NODE_DEFAULT_MODE, default_execution_mode)
    builder.add_node(NODE_BOOTSTRAP, bootstrap)
    builder.add_node(NODE_SPEC, spec_agent)
    builder.add_node(NODE_SCOUT, repo_scout)
    builder.add_node(NODE_PERMISSION, permission_guardian)
    builder.add_node(NODE_WORKTREE, prepare_worktree)
    builder.add_node(NODE_PREFLIGHT, write_preflight)
    builder.add_node(NODE_ARCHITECTURE, architecture_agent)
    builder.add_node(NODE_SELECT_PAGE, select_next_page)
    builder.add_node(NODE_PAGE_REBUILD, page_rebuild)
    builder.add_node(NODE_PAGE_CHECKPOINT, page_checkpoint)
    builder.add_node(NODE_MARK_COMPLETE, mark_page_complete)
    builder.add_node(NODE_FINAL_PERMISSION, final_permission_check)
    builder.add_node(NODE_HUMAN_GATE, human_gate)
    builder.add_node(NODE_RELEASE, release_agent)

    builder.add_edge(START, NODE_DEFAULT_MODE)
    builder.add_edge(NODE_DEFAULT_MODE, NODE_BOOTSTRAP)
    builder.add_edge(NODE_BOOTSTRAP, NODE_SPEC)
    builder.add_edge(NODE_SPEC, NODE_SCOUT)
    builder.add_edge(NODE_SCOUT, NODE_PERMISSION)
    builder.add_edge(NODE_PERMISSION, NODE_WORKTREE)
    builder.add_edge(NODE_WORKTREE, NODE_PREFLIGHT)
    builder.add_edge(NODE_PREFLIGHT, NODE_ARCHITECTURE)
    builder.add_edge(NODE_ARCHITECTURE, NODE_SELECT_PAGE)

    builder.add_conditional_edges(
        NODE_SELECT_PAGE,
        _page_router,
        {
            "重建页面": NODE_PAGE_REBUILD,
            "最终检查": NODE_FINAL_PERMISSION,
        },
    )
    builder.add_conditional_edges(
        NODE_PAGE_REBUILD,
        _after_page_rebuild,
        {
            "页面通过": NODE_PAGE_CHECKPOINT,
            "人工介入": NODE_FINAL_PERMISSION,
        },
    )
    builder.add_edge(NODE_PAGE_CHECKPOINT, NODE_MARK_COMPLETE)
    builder.add_edge(NODE_MARK_COMPLETE, NODE_SELECT_PAGE)
    builder.add_edge(NODE_FINAL_PERMISSION, NODE_HUMAN_GATE)

    builder.add_conditional_edges(
        NODE_HUMAN_GATE,
        _human_router,
        {"发布": NODE_RELEASE, "结束": END},
    )
    builder.add_edge(NODE_RELEASE, END)
    return builder.compile(checkpointer=checkpointer)


def initial_state(
    *,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    thread_id: str = "burncloud-ui-rebuild-v0.4",
    model_name: str = DEFAULT_MODEL_NAME,
    page_limit: int | None = DEFAULT_POLICY.default_page_limit,
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
        "completed_pages": [],
        "implementation_results": [],
        "page_checkpoint_history": [],
        "warnings": [],
        "phase": "start",
    }
    if page_limit is not None:
        state["page_limit"] = page_limit
    return state


# Exported Agent Server / Studio graph. Agent Server injects persistence at runtime.
graph = build_graph()
