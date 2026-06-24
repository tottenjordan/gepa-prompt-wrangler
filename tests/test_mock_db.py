"""Tests for MCP mock databases — seeded data integrity for eval cases."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "multi_model_agents"

sys.path.insert(0, str(EXAMPLE_ROOT))
from mcp_servers.expense import mock_db as expense_db


# ---------------------------------------------------------------------------
# Expense mock DB seeding
# ---------------------------------------------------------------------------
class TestExpenseMockDbSeed:
    def test_expenses_non_empty(self):
        assert expense_db.expenses, "expense mock_db.expenses must be pre-seeded"

    def test_emp001_has_records(self):
        assert len(expense_db.get_expenses("EMP001")) >= 1

    def test_emp002_has_records(self):
        assert len(expense_db.get_expenses("EMP002")) >= 1

    def test_at_least_one_over_limit_record(self):
        over_limit = [
            e for e in expense_db.expenses.values()
            if e["status"] == "pending_review"
        ]
        assert over_limit, (
            "at least one seeded expense must be over its policy limit "
            "(status == 'pending_review') so policy-violation eval cases have signal"
        )

    def test_seed_records_have_submit_expense_shape(self):
        expected_keys = {
            "expense_id", "amount", "category", "description",
            "user_id", "status", "policy_check", "submitted_at",
        }
        for e in expense_db.expenses.values():
            assert set(e.keys()) == expected_keys, (
                f"seed record {e.get('expense_id')} has keys {set(e.keys())}, "
                f"expected {expected_keys}"
            )

    def test_status_matches_policy_check(self):
        for e in expense_db.expenses.values():
            limit = expense_db.POLICY_LIMITS[e["category"].lower()]
            expected_status = "approved" if e["amount"] <= limit else "pending_review"
            assert e["status"] == expected_status, (
                f"{e['expense_id']}: status {e['status']} inconsistent with "
                f"amount {e['amount']} vs limit {limit}"
            )

    def test_policy_check_matches_check_policy(self):
        for e in expense_db.expenses.values():
            assert e["policy_check"] == expense_db.check_policy(
                e["amount"], e["category"]
            ), f"{e['expense_id']}: policy_check drift"
