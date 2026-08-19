from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import dotenv_values, find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI


# CLI commands need the same local .env behavior as `langgraph dev`. Existing
# process environment wins by design so deployment-injected secrets are never
# silently replaced by a local file.
load_dotenv(override=False)


# OpenAI-compatible model requests may occasionally fail with transient 5xx/
# connection/rate-limit errors. The OpenAI Python client retries those classes
# automatically; raise the default from the SDK default (2) to a more resilient
# Harness default. Callers can still override max_retries explicitly.
DEFAULT_MODEL_MAX_RETRIES = 6


@dataclass(frozen=True)
class RuntimeSecrets:
    """Runtime-only secrets and endpoint configuration.

    Secret fields are excluded from repr() so accidental logging does not reveal them.
    """

    api_key: str = field(repr=False)
    base_url: str
    langsmith_api_key: str = field(repr=False)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill in the value locally."
        )
    return value


def _fingerprint(value: str) -> str:
    """Return a short non-reversible identifier suitable for safe diagnostics."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def load_runtime_secrets() -> RuntimeSecrets:
    """Load the three approved runtime parameters from the process environment."""
    return RuntimeSecrets(
        api_key=_required_env("API_KEY"),
        base_url=_required_env("BASE_URL").rstrip("/"),
        langsmith_api_key=_required_env("LANGSMITH_API_KEY"),
    )


def runtime_diagnostics() -> dict[str, Any]:
    """Describe active model configuration without ever exposing secret values."""
    runtime = load_runtime_secrets()
    dotenv_path = find_dotenv(usecwd=True)
    dotenv_api_key = ""
    if dotenv_path:
        dotenv_api_key = str(dotenv_values(dotenv_path).get("API_KEY") or "").strip()

    return {
        "base_url": runtime.base_url,
        "api_key_length": len(runtime.api_key),
        "api_key_fingerprint": _fingerprint(runtime.api_key),
        "dotenv_found": bool(dotenv_path),
        "dotenv_api_key_present": bool(dotenv_api_key),
        "matches_dotenv": bool(dotenv_api_key) and runtime.api_key == dotenv_api_key,
        "default_model_max_retries": DEFAULT_MODEL_MAX_RETRIES,
    }


def create_chat_model(model_name: str, **kwargs: Any) -> ChatOpenAI:
    """Create a LangChain chat model using the configured OpenAI-compatible endpoint.

    The model name is intentionally supplied by the calling Agent rather than stored as
    another environment variable. This keeps the runtime secret surface limited to the
    three approved parameters: API_KEY, BASE_URL and LANGSMITH_API_KEY.
    """
    if not model_name.strip():
        raise ValueError("model_name must not be empty")

    runtime = load_runtime_secrets()
    kwargs.setdefault("max_retries", DEFAULT_MODEL_MAX_RETRIES)
    return ChatOpenAI(
        model=model_name.strip(),
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        **kwargs,
    )
