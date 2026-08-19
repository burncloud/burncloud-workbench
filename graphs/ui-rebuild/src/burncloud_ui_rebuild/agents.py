from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from burncloud_ui_rebuild.model_factory import create_chat_model


PROBE_VALUE = "burncloud-agent-ready"
PROBE_TOOL_RESULT = f"probe:{PROBE_VALUE}"


@tool
def agent_probe(value: str) -> str:
    """Return a deterministic probe value used to verify Agent tool calling."""
    return f"probe:{value}"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def build_probe_agent(model_name: str):
    """Build the smallest real create_agent() instance used for endpoint verification."""
    return create_agent(
        model=create_chat_model(model_name, timeout=60),
        tools=[agent_probe],
        system_prompt=(
            "You are the BurnCloud Agent connectivity probe. "
            "You MUST call the agent_probe tool exactly once with the value "
            f"'{PROBE_VALUE}'. After receiving the tool result, reply with AGENT_READY."
        ),
    )


def run_agent_check(model_name: str) -> dict[str, Any]:
    """Verify model invocation plus one real tool round trip without repository access."""
    agent = build_probe_agent(model_name)
    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "Run the required connectivity probe now. "
                    "Do not skip the tool call and do not perform any other task."
                ),
            }
        ]
    })
    messages = list(result.get("messages", []))
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    tool_called = any(PROBE_TOOL_RESULT in _message_text(message) for message in tool_messages)
    final_text = _message_text(messages[-1]) if messages else ""
    ready = tool_called and "AGENT_READY" in final_text
    return {
        "status": "PASS" if ready else "FAIL",
        "model": model_name,
        "tool_called": tool_called,
        "final_text": final_text,
    }
