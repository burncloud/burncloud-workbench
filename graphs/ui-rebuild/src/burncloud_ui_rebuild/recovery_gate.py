from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from .state import UIRebuildState


def recovery_confirmation_gate(state: UIRebuildState) -> dict[str, Any]:
    """Require an explicit HITL decision before any Studio-driven Git recovery.

    CLI recovery has its own `--confirm` boundary. This node protects Agent Server /
    Studio runs: a requested checkpoint restore never silently proceeds or silently
    falls through into new implementation work.
    """
    request = dict(state.get("recovery_request", {}))
    target = str(request.get("target_commit", "")).strip()
    if not target:
        return {"recovery_request": request}
    if bool(request.get("confirmed", False)):
        return {"recovery_request": request}

    decision = interrupt({
        "type": "burncloud_graph_engineering_v1_recovery_gate",
        "target_commit": target,
        "agent_branch": state.get("agent_branch", ""),
        "source_repo_root": state.get("source_repo_root", ""),
        "warning": "Recovery resets tracked changes on the current Agent branch to the selected known page checkpoint. Untracked files are preserved.",
        "question": "Approve recovery to this page checkpoint before continuing the run?",
    })
    if bool(decision):
        request["confirmed"] = True
        return {"recovery_request": request}

    return {
        "recovery_request": {},
        "recovery_result": {
            "status": "cancelled_by_human",
            "target_commit": target,
        },
    }
