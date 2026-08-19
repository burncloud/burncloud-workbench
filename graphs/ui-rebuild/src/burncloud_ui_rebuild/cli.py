from __future__ import annotations

import argparse
import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from burncloud_ui_rebuild.agents import run_agent_check
from burncloud_ui_rebuild.config import DEFAULT_MODEL_NAME
from burncloud_ui_rebuild.graph import build_graph, initial_state


def _print_run_result(result: dict, *, approve: bool, graph, config: dict) -> None:
    if "__interrupt__" in result:
        print(json.dumps({
            "status": "awaiting_human_gate",
            "model_name": result.get("model_name", DEFAULT_MODEL_NAME),
            "agent_branch": result.get("agent_branch", ""),
            "worktree_root": result.get("worktree_root", ""),
            "completed_pages": len(result.get("completed_pages", [])),
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
            "worktree_root": result.get("worktree_root", ""),
            "completed_pages": len(result.get("completed_pages", [])),
            "changed_files": result.get("changed_files", []),
            "validation_results": result.get("validation_results", []),
            "warnings": result.get("warnings", []),
        }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="BurnCloud UI Rebuild LangGraph")
    sub = parser.add_subparsers(dest="command", required=True)

    dry = sub.add_parser("dry-run", help="Run all 25 target page tasks without modifying burncloud/burncloud.")
    dry.add_argument("--thread-id", default="burncloud-ui-rebuild-v0.3")
    dry.add_argument("--approve", action="store_true", help="Automatically approve the final human gate.")

    check = sub.add_parser(
        "agent-check",
        help="Verify create_agent model access and tool calling without modifying source files.",
    )
    check.add_argument(
        "--model",
        default=DEFAULT_MODEL_NAME,
        help=f"Model exposed by BASE_URL. Defaults to {DEFAULT_MODEL_NAME}.",
    )

    rebuild = sub.add_parser(
        "rebuild",
        help="Create an isolated Agent branch/worktree and run real Builder/Verifier/Reviewer/Fixer Agents there.",
    )
    rebuild.add_argument(
        "--model",
        default=DEFAULT_MODEL_NAME,
        help=f"Model exposed by BASE_URL. Defaults to {DEFAULT_MODEL_NAME}.",
    )
    rebuild.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of target pages for this run. Defaults to 1 for safe first execution.",
    )
    rebuild.add_argument("--thread-id", default="burncloud-ui-rebuild-live-v0.3")
    rebuild.add_argument(
        "--write",
        action="store_true",
        help="Required acknowledgement that Agents may modify a newly-created isolated BurnCloud worktree.",
    )
    rebuild.add_argument(
        "--approve",
        action="store_true",
        help="Resume the final Human Gate. This still does not commit, push, merge, or modify main.",
    )

    args = parser.parse_args()

    if args.command == "agent-check":
        result = run_agent_check(args.model)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "PASS":
            raise SystemExit(1)
        return

    if args.command == "dry-run":
        graph = build_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(
            initial_state(execution_mode="dry_run", thread_id=args.thread_id),
            config=config,
        )
        _print_run_result(result, approve=args.approve, graph=graph, config=config)
        return

    if args.command == "rebuild":
        if not args.write:
            parser.error("rebuild requires --write because live Builder/Fixer Agents can modify the isolated Agent worktree")
        if args.limit < 1 or args.limit > 25:
            parser.error("--limit must be between 1 and 25")

        graph = build_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(
            initial_state(
                execution_mode="write",
                thread_id=args.thread_id,
                model_name=args.model,
                page_limit=args.limit,
            ),
            config=config,
        )
        _print_run_result(result, approve=args.approve, graph=graph, config=config)
        return


if __name__ == "__main__":
    main()
