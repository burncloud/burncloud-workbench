from __future__ import annotations

import time
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from .config import DEFAULT_MODEL_NAME
from .graph import build_graph, initial_state
from .policy import DEFAULT_POLICY, blocking_findings


def _summary(result: dict[str, Any], *, run_no: int) -> dict[str, Any]:
    return {
        "run": run_no,
        "status": result.get("release_status", result.get("phase", "")),
        "branch": result.get("agent_branch", ""),
        "current_page_status": result.get("current_page_status", ""),
        "completed_pages": len(result.get("completed_pages", [])),
        "run_tokens": int(result.get("budget_usage", {}).get("total_tokens", 0) or 0),
        "task_tokens": int(result.get("task_total_tokens", 0) or 0),
        "continuation_runs": int(result.get("continuation_runs", 0) or 0),
        "pull_request_number": int(result.get("pull_request_number", 0) or 0),
        "pull_request_url": result.get("pull_request_url", ""),
    }


def run_autopilot(
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    page_limit: int = 1,
    start_new_task: bool = False,
    max_runs: int | None = None,
) -> dict[str, Any]:
    """Run one engineering Task across multiple bounded Graph Runs.

    Each continuation uses a fresh in-memory LangGraph thread but restores compact
    Task state from the Agent branch Task Store. Clean final gates are auto-approved
    by graph policy; real blockers still interrupt for a human.
    """
    ceiling = max_runs or (DEFAULT_POLICY.max_continuation_runs + 1)
    history: list[dict[str, Any]] = []

    for run_no in range(1, ceiling + 1):
        thread_id = f"burncloud-autopilot-{int(time.time())}-{run_no}"
        graph = build_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(
            initial_state(
                execution_mode="write",
                thread_id=thread_id,
                model_name=model_name,
                page_limit=page_limit,
                start_new_task=start_new_task if run_no == 1 else False,
                autopilot_mode=True,
            ),
            config=config,
        )
        history.append(_summary(result, run_no=run_no))

        if "__interrupt__" in result:
            blockers = list(blocking_findings(result.get("final_findings", [])))
            return {
                "status": "human_required",
                "reason": result.get("last_failure_reason", "") or "Graph reached a human exception gate.",
                "blockers": blockers,
                "history": history,
                "agent_branch": result.get("agent_branch", ""),
            }

        status = str(result.get("release_status", result.get("phase", "")))
        if status == "continuation_required":
            continue

        return {
            "status": status or "completed",
            "history": history,
            "agent_branch": result.get("agent_branch", ""),
            "pull_request_number": result.get("pull_request_number", 0),
            "pull_request_url": result.get("pull_request_url", ""),
            "task_total_tokens": result.get("task_total_tokens", 0),
        }

    return {
        "status": "continuation_limit_exhausted",
        "history": history,
        "reason": f"Autopilot reached {ceiling} bounded Runs without completion.",
    }
