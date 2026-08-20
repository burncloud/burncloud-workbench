from burncloud_ui_rebuild.scenario_simulator import SCENARIOS, run_scenarios


def test_stabilization_scenarios_all_pass_without_model_calls():
    result = run_scenarios()
    assert len(SCENARIOS) >= 12
    assert result["status"] == "PASS"
    assert result["passed"] == result["total"]
