from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


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


def load_runtime_secrets() -> RuntimeSecrets:
    """Load the three approved runtime parameters from local environment or .env."""
    load_dotenv(override=False)
    return RuntimeSecrets(
        api_key=_required_env("API_KEY"),
        base_url=_required_env("BASE_URL").rstrip("/"),
        langsmith_api_key=_required_env("LANGSMITH_API_KEY"),
    )


def create_chat_model(model_name: str, **kwargs: Any) -> ChatOpenAI:
    """Create a LangChain chat model using the configured OpenAI-compatible endpoint.

    The model name is intentionally supplied by the calling Agent rather than stored as
    another environment variable. This keeps the runtime secret surface limited to the
    three approved parameters: API_KEY, BASE_URL and LANGSMITH_API_KEY.
    """
    if not model_name.strip():
        raise ValueError("model_name must not be empty")

    runtime = load_runtime_secrets()
    return ChatOpenAI(
        model=model_name.strip(),
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        **kwargs,
    )
