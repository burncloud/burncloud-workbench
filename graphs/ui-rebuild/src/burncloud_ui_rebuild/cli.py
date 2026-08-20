from __future__ import annotations

import argparse
import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.agents import run_agent_check
from burncloud_ui_rebuild.coding_tools import checkpoint_history, restore_page_checkpoint
from burncloud_ui_rebuild.config import DEFAULT_MODEL_NAME, source_root
from burncloud_ui_rebuild.graph import build_graph, initial_state
from burncloud_ui_rebuild.notifications import telegram_check
from burncloud_ui_rebuild.studio_supervisor import run_studio_supervisor
from burncloud_ui_rebuild.worktree import current_branch, migrate_legacy_agent_worktree


def _print_run_result(result: dict, *, approve: bool, graph, config: dict) -> None:
    if "__interrupt__" in result:
        print(json.dumps({
            "status": "awaiting_human_gate",
            "model_name": result.get("model_name", DEFAULT_MODEL_NAME),
            "agent_branch": result.get("agent_branch", ""),
            "source_repo_root": result.get("source_repo_root", ""),
            "completed_pages": len(result.get("completed_pages", [])),
            "current_page_status": result.get("current_page_status", ""),
            "budget_usage": result.get("budget_usage", {}),
            "notification_history": result.get("notification_history", []),
            "changed_files": result.get("changed_files", []),
            "validation_results": result.get("validation_results", []),
            "interrupt": str(result["__interrupt__"]),
        }, ensure_ascii=False, indent=2))
        if approve:
            result = graph.invoke(Command(resume=True), config=config)

    if "__interrupt__" not in result:
        print(json.dumps({
            "status": result.get("release_status", result.get("phase")),
            "model_name": result.get("model_name", DEFAULT_MODEL_NAME),
            "agent_branch": result.get("agent_branch", ""),
            "source_repo_root": result.get("source_repo_root", ""),
            "branch_task_status": result.get("branch_task_status", ""),
            "completed_pages": len(result.get("completed_pages", [])),
            "current_page_status": result.get("current_page_status", ""),
            "budget_usage": result.get("budget_usage", {}),
            "notification_history": result.get("notification_history", []),
            "changed_files": result.get("changed_files", []),
            "validation_results": result.get("validation_results", []),
            "warnings": result.get("warnings", []),
        }, ensure_ascii=False, indent=2))


def _current_agent_repo() -> str:
    root = source_root().resolve()
    branch = current_branch(root)
    if not branch.startswith("agent/ui-rebuild/"):
        raise SystemExit(
            f"BurnCloud checkout is on {branch!r}; checkpoints/recovery require the current agent/ui-rebuild/* branch."
        )
    return str(root)


def main() -> None:
    parser = argparse.ArgumentParser(description="BurnCloud Graph Engineering Harness v1")
    sub = parser.add_subparsers(dest="command", required=True)

    dry = sub.add_parser("dry-run", help="Run target page tasks without modifying burncloud/burncloud.")
    dry.add_argument("--thread-id", default="burncloud-graph-engineering-v1-dry")
    dry.add_argument("--limit", type=int, default=1)
    dry.add_argument("--approve", action="store_true", help="Automatically approve the final human gate.")

    check = sub.add_parser("agent-check", help="Verify model access and tool calling without source writes.")
    check.add_argument("--model", default=DEFAULT_MODEL_NAME, help=f"Defaults to {DEFAULT_MODEL_NAME}.")

    sub.add_parser("telegram-check", help="Send one Telegram test notification using local environment secrets.")
    sub.add_parser("studio", help="Run langgraph dev under the Telegram-aware Studio supervisor.")

    migrate = sub.add_parser("migrate-legacy-worktree", help="Move the previous Agent branch/worktree back into the primary BurnCloud checkout once.")
    migrate.add_argument("--confirm", action="store_true", help="Required because dirty legacy changes may be temporarily stashed and restored.")

    rebuild = sub.add_parser("rebuild", help="Run the real v1 Scout→Plan→Build→Verify→Review graph.")
    rebuild.add_argument("--model", default=DEFAULT_MODEL_NAME, help=f"Defaults to {DEFAULT_MODEL_NAME}.")
    rebuild.add_argument("--limit", type=int, default=1, help="Maximum pages for this bounded run.")
    rebuild.add_argument("--thread-id", default="burncloud-graph-engineering-v1-live")
    rebuild.add_argument("--write", action="store_true", help="Acknowledge writes to the current Agent branch.")
    rebuild.add_argument("--new-task", action="store_true", help="Start a fresh Agent branch from main. Dirty active branches are never abandoned automatically.")
    rebuild.add_argument("--approve", action="store_true", help="Resume the final Human Gate.")

    sub.add_parser("checkpoints", help="List page checkpoint commits on the current Agent branch.")

    recover = sub.add_parser("recover", help="Restore the current Agent branch to a known page checkpoint.")
    recover.add_argument("--commit", required=True, help="Exact checkpoint commit shown by the checkpoints command.")
    recover.add_argument("--confirm", action="store_true", help="Required destructive confirmation for tracked Agent-branch changes.")

    args = parser.parse_args()

    if args.command == "agent-check":
        result = run_agent_check(args.model)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "PASS":
            raise SystemExit(1)
        return

    if args.command == "telegram-check":
        result = telegram_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") != "sent":
            raise SystemExit(1)
        return

    if args.command == "studio":
        raise SystemExit(run_studio_supervisor())

    if args.command == "migrate-legacy-worktree":
        if not args.confirm:
            parser.error("migrate-legacy-worktree requires --confirm")
        result = migrate_legacy_agent_worktree(source_root())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "checkpoints":
        root = _current_agent_repo()
        print(json.dumps({"source_repo_root": root, "checkpoints": checkpoint_history(root)}, ensure_ascii=False, indent=2))
        return

    if args.command == "recover":
        if not args.confirm:
            parser.error("recover requires --confirm because it resets tracked Agent-branch changes")
        root = _current_agent_repo()
        result = restore_page_checkpoint(root, args.commit)
        print(json.dumps({"source_repo_root": root, "recovery": result}, ensure_ascii=False, indent=2))
        return

    if args.limit < 1 or args.limit > 25:
        parser.error("--limit must be between 1 and 25")

    if args.command == "dry-run":
        graph = build_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(
            initial_state(execution_mode="dry_run", thread_id=args.thread_id, page_limit=args.limit),
            config=config,
        )
        _print_run_result(result, approve=args.approve, graph=graph, config=config)
        return

    if args.command == "rebuild":
        if not args.write:
            parser.error("rebuild requires --write because Builder/Fixer may modify the current Agent branch")
        graph = build_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(
            initial_state(
                execution_mode="write",
                thread_id=args.thread_id,
                model_name=args.model,
                page_limit=args.limit,
                start_new_task=args.new_task,
            ),
            config=config,
        )
        _print_run_result(result, approve=args.approve, graph=graph, config=config)
        return


if __name__ == "__main__":
    main()
