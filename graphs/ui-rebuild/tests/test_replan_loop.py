from burncloud_ui_rebuild.page_graph import _after_fix, _prepare_replan


def test_fix_blocked_replans_before_plan_budget_is_exhausted():
    state = {
        "current_page_status": "fix_blocked",
        "plan_round": 1,
    }
    assert _after_fix(state) == "重新规划"


def test_fix_blocked_escalates_after_last_plan_round():
    state = {
        "current_page_status": "fix_blocked",
        "plan_round": 2,
    }
    assert _after_fix(state) == "人工介入"


def test_prepare_replan_converts_quality_blockers_to_planner_feedback():
    state = {
        "verification_findings": [],
        "review_findings": [
            {
                "severity": "major",
                "code": "BUYER-ROUTE-001",
                "message": "Buyer Overview is not reachable at /console/buyer.",
                "evidence": "route wiring is incomplete",
                "expected": "Wire the contracted buyer route.",
            },
            {
                "severity": "minor",
                "code": "COPY-001",
                "message": "Advisory copy polish.",
            },
        ],
        "fixer_report": {"status": "fix_blocked", "summary": "Current plan was insufficient."},
        "last_failure_reason": "BUYER-ROUTE-001",
    }

    update = _prepare_replan(state)

    assert update["current_page_status"] == "replan_requested"
    assert update["verification_findings"] == []
    assert update["review_findings"] == []
    assert len(update["plan_findings"]) == 1
    assert update["plan_findings"][0]["code"] == "REPLAN_BUYER-ROUTE-001"
    assert "route wiring is incomplete" in update["plan_findings"][0]["evidence"]
