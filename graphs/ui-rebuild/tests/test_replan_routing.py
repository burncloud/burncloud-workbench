from burncloud_ui_rebuild.page_graph import _after_review, _prepare_replan
from burncloud_ui_rebuild.policy import DEFAULT_POLICY


def test_reviewer_replans_when_blocker_cites_client_file_outside_plan():
    state = {
        "plan_round": 1,
        "implementation_plan": {
            "allowed_files": [
                "crates/client/src/critical_pages/buyer_overview.rs",
                "crates/client/src/product_ui.css",
            ]
        },
        "review_findings": [
            {
                "severity": "blocker",
                "code": "ROUTE-001",
                "message": "Buyer route is missing.",
                "evidence": "`crates/client/src/app.rs:18-60` has no /console/buyer route.",
                "expected": "Register /console/buyer in crates/client/src/app.rs.",
            }
        ],
    }
    assert _after_review(state) == "重新规划"


def test_reviewer_keeps_fixer_for_blocker_inside_current_plan():
    state = {
        "plan_round": 1,
        "implementation_plan": {
            "allowed_files": ["crates/client/src/critical_pages/buyer_overview.rs"]
        },
        "review_findings": [
            {
                "severity": "major",
                "code": "COPY-001",
                "message": "Buyer copy is misleading.",
                "evidence": "crates/client/src/critical_pages/buyer_overview.rs renders the unsupported claim.",
                "expected": "Correct the copy in the existing page file.",
            }
        ],
    }
    assert _after_review(state) == "修复"


def test_reviewer_does_not_replan_after_plan_budget_is_exhausted():
    state = {
        "plan_round": DEFAULT_POLICY.max_plan_rounds,
        "implementation_plan": {
            "allowed_files": ["crates/client/src/critical_pages/buyer_overview.rs"]
        },
        "review_findings": [
            {
                "severity": "blocker",
                "code": "ROUTE-001",
                "message": "Buyer route is missing.",
                "evidence": "crates/client/src/app.rs:18-60",
            }
        ],
    }
    assert _after_review(state) == "修复"


def test_prepare_replan_resets_fix_round_for_new_plan():
    update = _prepare_replan(
        {
            "plan_round": 1,
            "fix_round": 3,
            "verification_findings": [],
            "review_findings": [
                {
                    "severity": "blocker",
                    "code": "ROUTE-001",
                    "message": "Buyer route is missing.",
                    "evidence": "crates/client/src/app.rs:18-60",
                    "expected": "Add the route.",
                }
            ],
        }
    )
    assert update["fix_round"] == 0
    assert update["current_page_status"] == "replan_requested"
    assert update["plan_findings"][0]["code"] == "REPLAN_ROUTE-001"
