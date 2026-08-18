from __future__ import annotations

import argparse
import json

from langgraph.types import Command

from .graph import graph, initial_state


def main() -> None:
    parser = argparse.ArgumentParser(description="BurnCloud UI Rebuild LangGraph")
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run", help="Run all roles without modifying burncloud/burncloud.")
    dry.add_argument("--thread-id", default="burncloud-ui-rebuild-v0.1")
    dry.add_argument("--approve", action="store_true", help="Automatically approve the final human gate.")
    args = parser.parse_args()

    if args.command == "dry-run":
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke(
            initial_state(execution_mode="dry_run", thread_id=args.thread_id),
            config=config,
        )
        if "__interrupt__" in result:
            print(json.dumps({
                "status": "awaiting_human_gate",
                "completed_pages": len(result.get("completed_pages", [])),
                "interrupt": str(result["__interrupt__"]),
            }, indent=2))
            if args.approve:
                result = graph.invoke(Command(resume=True), config=config)

        if "__interrupt__" not in result:
            print(json.dumps({
                "status": result.get("release_status", result.get("phase")),
                "completed_pages": len(result.get("completed_pages", [])),
                "warnings": result.get("warnings", []),
            }, indent=2))


if __name__ == "__main__":
    main()
