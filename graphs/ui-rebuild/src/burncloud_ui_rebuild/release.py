from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .coding_tools import checkpoint_history
from .notifications import send_telegram_message
from .state import UIRebuildState
from .worktree import AGENT_BRANCH_PREFIX, current_branch, mark_agent_branch_completed, porcelain_status


class ReleaseError(RuntimeError):
    pass


def _run(root: Path, argv: list[str], *, timeout: int = 120) -> str:
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ReleaseError(f"Required executable not found: {argv[0]}") from exc
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        raise ReleaseError(f"{' '.join(argv)} failed ({completed.returncode}): {output}")
    return output


def release_preflight(state: UIRebuildState) -> dict[str, Any]:
    """Fail early if a successful run could not be published to GitHub later."""
    if state.get("execution_mode", "dry_run") != "write":
        return {"release_preflight": {"status": "dry_run"}, "phase": "release_preflight_skipped"}

    root = Path(str(state.get("source_repo_root", ""))).resolve()
    if not root.exists():
        raise ReleaseError(f"BurnCloud source repo does not exist: {root}")
    if shutil.which("gh") is None:
        raise ReleaseError("GitHub CLI `gh` is required. Install it and run `gh auth login` before starting a write Graph.")

    origin = _run(root, ["git", "remote", "get-url", "origin"], timeout=30).strip()
    if not origin:
        raise ReleaseError("Git remote `origin` is missing; completed runs cannot be pushed or submitted as PRs.")
    if "github.com" not in origin.lower():
        raise ReleaseError(f"Git remote `origin` is not a GitHub remote: {origin}")

    # Do not persist auth output; it can contain account/scope details. Exit code is enough.
    _run(root, ["gh", "auth", "status"], timeout=30)
    return {
        "release_preflight": {
            "status": "ready",
            "origin": origin,
            "draft_pr": True,
            "auto_merge": False,
        },
        "phase": "release_preflight_passed",
    }


def release_preflight_node(state: UIRebuildState) -> dict[str, Any]:
    return release_preflight(state)


def _ensure_publishable_branch(root: Path, expected_branch: str, base_branch: str) -> None:
    actual = current_branch(root)
    if actual != expected_branch:
        raise ReleaseError(f"Release branch mismatch: expected {expected_branch!r}, current {actual!r}.")
    if actual in {"main", "master"} or not actual.startswith(AGENT_BRANCH_PREFIX):
        raise ReleaseError(f"Refusing Pull Request publish from non-Agent branch: {actual!r}.")
    status = porcelain_status(root)
    if status:
        raise ReleaseError(
            "Completed Graph must have a clean Agent branch before PR publication. "
            f"Current git status:\n{status}"
        )
    ahead = _run(root, ["git", "rev-list", "--count", f"{base_branch}..{expected_branch}"], timeout=30).strip()
    if not ahead.isdigit() or int(ahead) < 1:
        raise ReleaseError(f"Agent branch {expected_branch!r} has no commits ahead of {base_branch!r}.")


def _pr_title(pages: list[str]) -> str:
    if len(pages) == 1:
        return f"feat(ui): rebuild {pages[0]}"
    if pages:
        return f"feat(ui): rebuild {len(pages)} console pages"
    return "feat(ui): BurnCloud console rebuild"


def _pr_body(*, branch: str, base_branch: str, pages: list[str], diff_stat: str) -> str:
    page_lines = "\n".join(f"- `{page}`" for page in pages) if pages else "- Harness page checkpoints present on branch"
    return "\n".join([
        "## BurnCloud Graph Engineering Harness v1",
        "",
        "This Draft PR was created automatically after the bounded Graph completed and passed the Human Gate.",
        "",
        f"- Base: `{base_branch}`",
        f"- Agent branch: `{branch}`",
        "- Automatic merge: **disabled**",
        "",
        "### Completed page checkpoints",
        page_lines,
        "",
        "### Diff summary",
        "```text",
        diff_stat or "No diff stat available.",
        "```",
        "",
        "The Harness only publishes after deterministic code/reality checks and independent review. Final merge remains a separate human-controlled action.",
    ])


def _list_matching_prs(root: Path, branch: str, base_branch: str) -> list[dict[str, Any]]:
    output = _run(
        root,
        [
            "gh", "pr", "list",
            "--head", branch,
            "--base", base_branch,
            "--state", "all",
            "--json", "number,url,title,state,isDraft",
            "--limit", "20",
        ],
        timeout=60,
    )
    try:
        parsed = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"Could not parse `gh pr list` output: {output}") from exc
    return parsed if isinstance(parsed, list) else []


def _view_pr(root: Path, pr_number: int) -> dict[str, Any]:
    output = _run(
        root,
        ["gh", "pr", "view", str(pr_number), "--json", "number,url,title,state,isDraft"],
        timeout=60,
    )
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"Could not parse `gh pr view` output: {output}") from exc
    if not isinstance(parsed, dict):
        raise ReleaseError("Unexpected `gh pr view` response.")
    return parsed


def publish_pull_request(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {"release_status": "dry_run_complete_no_git_write", "phase": "done"}

    branch = str(state.get("agent_branch", "")).strip()
    base_branch = str(state.get("base_branch", "main")).strip() or "main"
    root = Path(str(state.get("source_repo_root", ""))).resolve()
    if not branch or not root.exists():
        raise ReleaseError("Release requires source_repo_root and agent_branch.")

    prior_completed = str(state.get("branch_task_status", "")) in {"completed_unintegrated", "awaiting_pr_merge"}
    if not bool(state.get("human_decision", False)) and not prior_completed:
        return {"release_status": "rejected", "phase": "done"}

    blockers = [item for item in state.get("final_findings", []) if item.get("severity") == "blocker"]
    if blockers and not prior_completed:
        return {"release_status": "blocked_by_final_findings", "phase": "done"}

    _ensure_publishable_branch(root, branch, base_branch)
    if shutil.which("gh") is None:
        raise ReleaseError("GitHub CLI `gh` is required for automatic Pull Request publication. Install it and run `gh auth login` once.")

    checkpoints = checkpoint_history(root)
    pages = [str(item.get("page_id", "")) for item in checkpoints if item.get("page_id")]
    diff_stat = _run(root, ["git", "diff", "--stat", f"{base_branch}...{branch}"], timeout=30)

    # Never force-push. Divergence or permission problems fail closed and are surfaced by the Graph error boundary.
    _run(root, ["git", "push", "--set-upstream", "origin", branch], timeout=300)

    existing = _list_matching_prs(root, branch, base_branch)
    selected: dict[str, Any] | None = None
    release_status = "pull_request_reused"

    open_prs = [item for item in existing if str(item.get("state", "")).upper() == "OPEN"]
    merged_prs = [item for item in existing if str(item.get("state", "")).upper() == "MERGED"]
    closed_prs = [item for item in existing if str(item.get("state", "")).upper() == "CLOSED"]

    if open_prs:
        selected = open_prs[0]
    elif merged_prs:
        selected = merged_prs[0]
        release_status = "pull_request_merged"
    elif closed_prs:
        closed = closed_prs[0]
        raise ReleaseError(
            "A matching Pull Request already exists but is closed without merge. "
            f"Refusing to reopen or create a duplicate automatically: {closed.get('url', closed)}"
        )
    else:
        title = _pr_title(pages)
        body = _pr_body(branch=branch, base_branch=base_branch, pages=pages, diff_stat=diff_stat)
        created_output = _run(
            root,
            [
                "gh", "pr", "create",
                "--draft",
                "--base", base_branch,
                "--head", branch,
                "--title", title,
                "--body", body,
            ],
            timeout=120,
        )
        match = re.search(r"https://github\.com/[^\s]+/pull/(\d+)", created_output)
        if not match:
            matches = _list_matching_prs(root, branch, base_branch)
            open_matches = [item for item in matches if str(item.get("state", "")).upper() == "OPEN"]
            if not open_matches:
                raise ReleaseError(f"PR creation returned no parseable PR URL: {created_output}")
            selected = open_matches[0]
        else:
            selected = _view_pr(root, int(match.group(1)))
        release_status = "pull_request_opened"

    if selected is None:
        raise ReleaseError("Could not resolve the Pull Request after publication.")

    number = int(selected.get("number", 0) or 0)
    url = str(selected.get("url", ""))
    if number < 1 or not url:
        raise ReleaseError(f"Resolved Pull Request is missing number/url: {selected}")

    mark_agent_branch_completed(root, branch)
    return {
        "release_status": release_status,
        "branch_task_status": "awaiting_pr_merge" if release_status != "pull_request_merged" else "merged_remote",
        "pull_request_number": number,
        "pull_request_url": url,
        "pull_request_title": str(selected.get("title", _pr_title(pages))),
        "pull_request_status": str(selected.get("state", "OPEN")).lower(),
        "phase": "done",
    }


def publish_pull_request_node(state: UIRebuildState) -> dict[str, Any]:
    return publish_pull_request(state)


def pull_request_completion_notification(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {}
    if state.get("release_status") not in {"pull_request_opened", "pull_request_reused"}:
        return {}

    thread_id = str(state.get("thread_id", "unknown"))
    url = str(state.get("pull_request_url", ""))
    number = int(state.get("pull_request_number", 0) or 0)
    branch = str(state.get("agent_branch", ""))
    usage = dict(state.get("budget_usage", {}))
    result = send_telegram_message(
        "\n".join([
            "✅ BurnCloud Harness 任务完成并已提交 Pull Request",
            f"PR: #{number}",
            f"链接: {url}",
            f"分支: {branch}",
            f"Token: {usage.get('total_tokens', 0)}",
            f"Thread: {thread_id}",
            "状态: Draft PR 已创建/复用；不会自动 merge main。",
        ]),
        event_key=f"pull-request:{branch}:{number}:{url}",
    )
    history = list(state.get("notification_history", []))
    history.append({"event": "pull_request", **result})
    return {"notification_history": history}
