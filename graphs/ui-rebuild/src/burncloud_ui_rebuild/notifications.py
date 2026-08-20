from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from functools import wraps
from typing import Any, Callable

from .state import UIRebuildState


TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
MAX_TELEGRAM_MESSAGE_CHARS = 3800
_SENT_EVENT_KEYS: set[str] = set()
_SENT_EVENT_LOCK = threading.Lock()


def telegram_configured() -> bool:
    return bool(os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip() and os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip())


def _redact(text: str) -> str:
    redacted = text
    for name in ("API_KEY", "LANGSMITH_API_KEY", TELEGRAM_BOT_TOKEN_ENV):
        value = os.environ.get(name, "").strip()
        if len(value) >= 6:
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", redacted)
    return redacted[:MAX_TELEGRAM_MESSAGE_CHARS]


def _claim_event(event_key: str) -> bool:
    if not event_key:
        return True
    with _SENT_EVENT_LOCK:
        if event_key in _SENT_EVENT_KEYS:
            return False
        _SENT_EVENT_KEYS.add(event_key)
        return True


def send_telegram_message(text: str, *, event_key: str = "", force: bool = False) -> dict[str, Any]:
    """Best-effort Telegram Bot API notification.

    Notification failure must never fail the Graph. Secrets are read only from the
    local environment and are never returned in the result.
    """
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    chat_id = os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip()
    if not token or not chat_id:
        return {"status": "disabled", "reason": "telegram_not_configured"}

    if not force and event_key and not _claim_event(event_key):
        return {"status": "deduplicated", "event_key": event_key}

    payload = json.dumps({"chat_id": chat_id, "text": _redact(text)}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - fixed Telegram HTTPS endpoint
            status_code = int(getattr(response, "status", 200))
            body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body) if body else {}
        if status_code >= 400 or not bool(parsed.get("ok", False)):
            return {"status": "failed", "http_status": status_code, "reason": "telegram_api_rejected"}
        return {"status": "sent", "http_status": status_code, "event_key": event_key}
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "http_status": int(exc.code), "reason": "telegram_http_error"}
    except Exception as exc:  # notification transport must never break the delivery graph
        return {"status": "failed", "reason": f"{type(exc).__name__}: {_redact(str(exc))}"}


def _append_history(state: UIRebuildState, event: str, result: dict[str, Any]) -> dict[str, Any]:
    history = list(state.get("notification_history", []))
    history.append({"event": event, **result})
    return {"notification_history": history}


def _page_id(state: UIRebuildState) -> str:
    page = state.get("current_page")
    if isinstance(page, dict):
        return str(page.get("id", ""))
    return str(state.get("page_context", {}).get("page_id", ""))


def notify_graph_error(state: UIRebuildState, node_name: str, exc: BaseException) -> dict[str, Any]:
    thread_id = str(state.get("thread_id", "unknown"))
    page_id = _page_id(state) or "-"
    branch = str(state.get("agent_branch", "")) or "-"
    error_text = _redact(f"{type(exc).__name__}: {exc}")
    event_key = f"error:{thread_id}:{node_name}:{page_id}:{type(exc).__name__}:{error_text[:160]}"
    message = "\n".join([
        "🚨 BurnCloud Harness 图错误",
        f"节点: {node_name}",
        f"页面: {page_id}",
        f"状态: {state.get('current_page_status', '-')}",
        f"分支: {branch}",
        f"Thread: {thread_id}",
        f"错误: {error_text}",
    ])
    return send_telegram_message(message, event_key=event_key)


def error_notifying_node(node_name: str, node: Callable[[UIRebuildState], dict[str, Any]]):
    """Wrap a normal node with a Telegram error boundary and re-raise the original error."""
    @wraps(node)
    def wrapped(state: UIRebuildState):
        try:
            return node(state)
        except BaseException as exc:
            # LangGraph interrupt is control flow, not an error. Interrupt nodes are
            # normally left unwrapped, and this guard protects future refactors.
            if type(exc).__name__ in {"GraphInterrupt", "NodeInterrupt"}:
                raise
            notify_graph_error(state, node_name, exc)
            raise

    return wrapped


def recovery_review_notification(state: UIRebuildState) -> dict[str, Any]:
    request = dict(state.get("recovery_request", {}))
    target = str(request.get("target_commit", "")).strip()
    if not target or bool(request.get("confirmed", False)):
        return {}
    if state.get("execution_mode", "dry_run") != "write":
        return {}

    thread_id = str(state.get("thread_id", "unknown"))
    result = send_telegram_message(
        "\n".join([
            "🟠 BurnCloud Harness 需要人工审核",
            "类型: Git Checkpoint 恢复确认",
            f"目标 Commit: {target}",
            f"分支: {state.get('agent_branch', '-')}",
            f"Thread: {thread_id}",
            "操作: 请在 LangGraph Studio 的恢复审批节点确认或拒绝。",
        ]),
        event_key=f"recovery-review:{thread_id}:{target}",
    )
    return _append_history(state, "recovery_review", result)


def human_review_notification(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {}

    thread_id = str(state.get("thread_id", "unknown"))
    page_id = _page_id(state) or "-"
    blockers = [
        item for item in state.get("final_findings", [])
        if str(item.get("severity", "")).lower() in {"blocker", "major"}
    ]
    first_reason = ""
    if blockers:
        first = blockers[0]
        first_reason = f"{first.get('code', 'UNKNOWN')}: {first.get('message', '')}"
    elif state.get("last_failure_reason"):
        first_reason = str(state.get("last_failure_reason"))
    else:
        first_reason = "页面/运行已到最终人工 Gate。"

    result = send_telegram_message(
        "\n".join([
            "🟡 BurnCloud Harness 需要人工审核",
            f"页面: {page_id}",
            f"状态: {state.get('current_page_status', '-')}",
            f"已完成: {len(state.get('completed_pages', []))}/{len(state.get('page_queue', []))}",
            f"阻塞项: {len(blockers)}",
            f"原因: {_redact(first_reason)}",
            f"分支: {state.get('agent_branch', '-')}",
            f"Thread: {thread_id}",
            "操作: 请打开 LangGraph Studio 查看人工审批节点。",
        ]),
        event_key=f"human-review:{thread_id}:{page_id}:{state.get('current_page_status', '')}:{len(state.get('completed_pages', []))}",
    )
    return _append_history(state, "human_review", result)


def completion_notification(state: UIRebuildState) -> dict[str, Any]:
    if state.get("execution_mode", "dry_run") != "write":
        return {}
    if state.get("release_status") != "approved_agent_branch_no_git_publish":
        return {}

    thread_id = str(state.get("thread_id", "unknown"))
    checkpoint = dict(state.get("page_checkpoint", {}))
    usage = dict(state.get("budget_usage", {}))
    result = send_telegram_message(
        "\n".join([
            "✅ BurnCloud Harness 任务完成",
            f"已完成页面: {len(state.get('completed_pages', []))}/{len(state.get('page_queue', []))}",
            f"分支: {state.get('agent_branch', '-')}",
            f"最新 Checkpoint: {checkpoint.get('commit', '-')}",
            f"Token: {usage.get('total_tokens', 0)}",
            f"Thread: {thread_id}",
            "状态: Agent 分支已完成并通过人工 Gate；未自动 push/merge main。",
        ]),
        event_key=f"completed:{thread_id}:{state.get('agent_branch', '')}:{checkpoint.get('commit', '')}",
    )
    return _append_history(state, "completed", result)


def telegram_check() -> dict[str, Any]:
    return send_telegram_message(
        "🧪 BurnCloud Graph Engineering Harness Telegram 通知测试成功。",
        event_key="telegram-check",
        force=True,
    )
