from burncloud_ui_rebuild.policy import DEFAULT_POLICY


def test_deep_repository_agent_budgets_are_expanded():
    assert DEFAULT_POLICY.scout_budget.max_model_calls == 90
    assert DEFAULT_POLICY.scout_budget.max_tool_calls == 240

    assert DEFAULT_POLICY.planner_budget.max_model_calls == 60
    assert DEFAULT_POLICY.planner_budget.max_tool_calls == 150

    assert DEFAULT_POLICY.builder_budget.max_model_calls == 120
    assert DEFAULT_POLICY.builder_budget.max_tool_calls == 300

    assert DEFAULT_POLICY.reviewer_budget.max_model_calls == 60
    assert DEFAULT_POLICY.reviewer_budget.max_tool_calls == 150

    assert DEFAULT_POLICY.fixer_budget.max_model_calls == 90
    assert DEFAULT_POLICY.fixer_budget.max_tool_calls == 240


def test_graph_token_budget_is_five_million():
    assert DEFAULT_POLICY.max_page_tokens == 5_000_000
    assert DEFAULT_POLICY.max_run_tokens == 5_000_000
