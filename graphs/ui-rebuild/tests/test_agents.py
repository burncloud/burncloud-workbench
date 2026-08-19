from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

import burncloud_ui_rebuild.agents as agents


class _FakeProbeAgent:
    def invoke(self, payload):
        assert payload["messages"]
        return {
            "messages": [
                ToolMessage(content=agents.PROBE_TOOL_RESULT, tool_call_id="probe-call"),
                AIMessage(content="AGENT_READY"),
            ]
        }


def test_agent_check_requires_real_tool_round_trip(monkeypatch):
    monkeypatch.setattr(agents, "build_probe_agent", lambda model_name: _FakeProbeAgent())

    result = agents.run_agent_check("test-model")

    assert result == {
        "status": "PASS",
        "model": "test-model",
        "tool_called": True,
        "final_text": "AGENT_READY",
    }


class _NoToolProbeAgent:
    def invoke(self, payload):
        return {"messages": [AIMessage(content="AGENT_READY")]}


def test_agent_check_fails_if_model_skips_tool(monkeypatch):
    monkeypatch.setattr(agents, "build_probe_agent", lambda model_name: _NoToolProbeAgent())

    result = agents.run_agent_check("test-model")

    assert result["status"] == "FAIL"
    assert result["tool_called"] is False
