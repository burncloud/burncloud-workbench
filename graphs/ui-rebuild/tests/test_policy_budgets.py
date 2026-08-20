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


def test_run_and_task_token_budgets_are_layered():
    assert DEFAULT_POLICY.max_page_tokens == 5_000_000
    assert DEFAULT_POLICY.max_run_tokens == 5_000_000
    assert DEFAULT_POLICY.max_task_tokens == 15_000_000
    assert DEFAULT_POLICY.max_continuation_runs == 4


def test_restore_cleanup_budget_is_independent_from_edit_budget():
    assert DEFAULT_POLICY.max_write_files_per_agent == 8
    assert DEFAULT_POLICY.max_plan_files == 8
    assert DEFAULT_POLICY.max_restore_files_per_agent == 128
