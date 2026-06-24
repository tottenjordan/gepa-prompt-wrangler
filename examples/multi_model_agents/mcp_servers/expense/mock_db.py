"""Mock expense database — in-memory store with corporate policy limit enforcement."""

import uuid
from datetime import datetime

POLICY_LIMITS = {
    "meals": 75.00,
    "transport": 200.00,
    "lodging": 400.00,
    "supplies": 100.00,
    "entertainment": 150.00,
}


def check_policy(amount: float, category: str) -> dict:
    category_lower = category.lower()
    if category_lower not in POLICY_LIMITS:
        return {
            "within_policy": False,
            "reason": f"Unknown category '{category}'. Valid: {', '.join(POLICY_LIMITS.keys())}",
        }
    limit = POLICY_LIMITS[category_lower]
    return {
        "within_policy": amount <= limit,
        "limit": limit,
        "amount": amount,
        "category": category_lower,
        "reason": None if amount <= limit else f"Amount ${amount:.2f} exceeds ${limit:.2f} limit for {category_lower}",
    }


def _seed(expense_id: str, amount: float, category: str, description: str,
          user_id: str, submitted_at: str) -> dict:
    """Build a seed record with the exact shape submit_expense() produces.

    status and policy_check are derived from check_policy() at module load so
    they never drift from the server's policy logic.
    """
    policy_check = check_policy(amount, category)
    return {
        "expense_id": expense_id,
        "amount": amount,
        "category": category,
        "description": description,
        "user_id": user_id,
        "status": "approved" if policy_check["within_policy"] else "pending_review",
        "policy_check": policy_check,
        "submitted_at": submitted_at,
    }


# Pre-seeded expenses so eval cases that reference expense history (get_user_expenses)
# and policy-violation flagging have real data to act on. Fixed string IDs (EX-001..)
# and static ISO submitted_at timestamps keep records deterministic across runs —
# unlike submit_expense() which uses uuid + datetime.now(). EMP001 lodging $500
# (limit 400) and EMP002 entertainment $200 (limit 150) are deliberately over-limit
# so "flag policy violations" cases get pending_review signal. Identities mirror the
# booking mock_db: EMP001=Alice Johnson, EMP002=Lisa Wang, EMP003=Bob Smith,
# EMP004=Carol Davis.
expenses: dict[str, dict] = {
    rec["expense_id"]: rec for rec in [
        _seed("EX-001", 42.50, "meals", "Team lunch with client", "EMP001",
              "2026-05-01T12:30:00"),
        _seed("EX-002", 500.00, "lodging", "Hotel stay - 2 nights downtown", "EMP001",
              "2026-05-02T18:00:00"),
        _seed("EX-003", 65.00, "transport", "Airport taxi round trip", "EMP002",
              "2026-05-03T09:15:00"),
        _seed("EX-004", 200.00, "entertainment", "Client dinner and event tickets", "EMP002",
              "2026-05-04T20:00:00"),
        _seed("EX-005", 38.00, "supplies", "Notebooks and presentation materials", "EMP003",
              "2026-05-05T10:00:00"),
        _seed("EX-006", 120.00, "transport", "Rental car for site visit", "EMP003",
              "2026-05-06T08:45:00"),
        _seed("EX-007", 70.00, "meals", "Working dinner solo", "EMP004",
              "2026-05-07T19:30:00"),
        _seed("EX-008", 95.00, "supplies", "Conference badge printer ink", "EMP004",
              "2026-05-08T11:00:00"),
    ]
}


def submit_expense(amount: float, category: str, description: str, user_id: str) -> dict:
    expense_id = f"EX-{uuid.uuid4().hex[:8].upper()}"
    policy_check = check_policy(amount, category)
    expense = {
        "expense_id": expense_id,
        "amount": amount,
        "category": category,
        "description": description,
        "user_id": user_id,
        "status": "approved" if policy_check["within_policy"] else "pending_review",
        "policy_check": policy_check,
        "submitted_at": datetime.now().isoformat(),
    }
    expenses[expense_id] = expense
    return expense


def get_expenses(user_id: str) -> list[dict]:
    return [e for e in expenses.values() if e["user_id"] == user_id]
