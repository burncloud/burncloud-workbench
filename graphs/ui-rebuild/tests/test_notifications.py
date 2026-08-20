from __future__ import annotations

import json

import pytest

import burncloud_ui_rebuild.notifications as notifications


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b'{"ok": true, "result": {"message_id": 1}}'


def _configure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_BOT_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99887766")
    notifications._SENT_EVENT_KEYS.clear()


def test_telegram_is_disabled_without_local_secrets(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    result = notifications.send_telegram_message("hello")

    assert result == {"status": "disabled", "reason": "telegram_not_configured"}


def test_telegram_payload_redacts_known_secrets(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("API_KEY", "super-secret-api-key")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(notifications.urllib.request, "urlopen", fake_urlopen)

    result = notifications.send_telegram_message(
        "error contains super-secret-api-key",
        event_key="redaction-test",
    )

    assert result["status"] == "sent"
    assert captured["payload"]["chat_id"] == "99887766"
    assert "super-secret-api-key" not in captured["payload"]["text"]
    assert "[REDACTED]" in captured["payload"]["text"]
    assert captured["timeout"] == 10


def test_event_key_deduplicates_same_notification(monkeypatch):
    _configure(monkeypatch)
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(notifications.urllib.request, "urlopen", fake_urlopen)

    first = notifications.send_telegram_message("one", event_key="same-event")
    second = notifications.send_telegram_message("one", event_key="same-event")

    assert first["status"] == "sent"
    assert second["status"] == "deduplicated"
    assert len(calls) == 1


def test_human_review_notification_records_history(monkeypatch):
    monkeypatch.setattr(
        notifications,
        "send_telegram_message",
        lambda *args, **kwargs: {"status": "sent", "event_key": kwargs.get("event_key", "")},
    )
    state = {
        "execution_mode": "write",
        "thread_id": "thread-1",
        "agent_branch": "agent/ui-rebuild/test",
        "current_page": {"id": "buyer-overview"},
        "current_page_status": "fix_blocked",
        "completed_pages": [],
        "page_queue": [{"id": "buyer-overview"}],
        "final_findings": [
            {"severity": "blocker", "code": "BUILD", "message": "compile failed"},
        ],
        "notification_history": [],
    }

    update = notifications.human_review_notification(state)

    assert update["notification_history"][-1]["event"] == "human_review"
    assert update["notification_history"][-1]["status"] == "sent"


def test_completion_notification_only_fires_for_successful_write_release(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifications,
        "send_telegram_message",
        lambda text, **kwargs: calls.append(text) or {"status": "sent"},
    )
    base = {
        "execution_mode": "write",
        "thread_id": "thread-2",
        "agent_branch": "agent/ui-rebuild/test",
        "completed_pages": ["buyer-overview"],
        "page_queue": [{"id": "buyer-overview"}],
        "page_checkpoint": {"commit": "abc123"},
        "budget_usage": {"total_tokens": 100},
        "notification_history": [],
    }

    assert notifications.completion_notification({**base, "release_status": "blocked_by_final_findings"}) == {}
    update = notifications.completion_notification({**base, "release_status": "approved_agent_branch_no_git_publish"})

    assert len(calls) == 1
    assert update["notification_history"][-1]["event"] == "completed"


def test_error_boundary_notifies_then_reraises(monkeypatch):
    captured = []
    monkeypatch.setattr(
        notifications,
        "notify_graph_error",
        lambda state, node_name, exc: captured.append((node_name, type(exc).__name__)) or {"status": "sent"},
    )

    def exploding_node(state):
        raise RuntimeError("boom")

    wrapped = notifications.error_notifying_node("实施修改", exploding_node)

    with pytest.raises(RuntimeError, match="boom"):
        wrapped({"thread_id": "thread-3"})

    assert captured == [("实施修改", "RuntimeError")]
