from __future__ import annotations

import pytest

from burncloud_ui_rebuild.model_factory import load_runtime_secrets


def test_runtime_secrets_load_only_approved_environment(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8080/v1/")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-langsmith-key")

    runtime = load_runtime_secrets()

    assert runtime.api_key == "test-api-key"
    assert runtime.base_url == "http://127.0.0.1:8080/v1"
    assert runtime.langsmith_api_key == "test-langsmith-key"


def test_runtime_secrets_do_not_leak_keys_in_repr(monkeypatch):
    monkeypatch.setenv("API_KEY", "super-secret-api-key")
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "super-secret-langsmith-key")

    runtime = load_runtime_secrets()
    rendered = repr(runtime)

    assert "super-secret-api-key" not in rendered
    assert "super-secret-langsmith-key" not in rendered
    assert "http://127.0.0.1:8080/v1" in rendered


def test_missing_required_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-langsmith-key")

    with pytest.raises(RuntimeError, match="API_KEY"):
        load_runtime_secrets()
