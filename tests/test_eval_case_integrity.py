"""Durable structural-integrity guard for eval_cases.yaml.

Cross-checks EVERY case in
``examples/multi_model_agents/eval_data/eval_cases.yaml`` against the REAL MCP
tool signatures (derived by introspecting the FastMCP servers) and the REAL
mock-DB contents (derived by importing the mock_db modules). Nothing is
hardcoded — if a tool signature or seeded ID changes, the ground truth used by
this test changes with it.

This is an anti-drift guard: when it fails, the assertion message enumerates
EVERY offending case so the data can be fixed in one pass.

The five checks (applied to each ``expected_tools`` entry of each case):

1. Tool name is a known tool.
2. Arg keys are a subset of the tool's full parameter set (no unknown args).
3. Every required param is present in args (sentinel ``"<runtime>"`` counts as
   present).
4. ``list_all_bookings`` must have empty args.
5. Referenced IDs exist in the mock DBs — skipped for ``error_handling`` cases
   and for any arg whose value is the ``"<runtime>"`` sentinel.
"""

import asyncio
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "multi_model_agents"
EVAL_DATA_DIR = EXAMPLE_ROOT / "eval_data"
YAML_PATH = EVAL_DATA_DIR / "eval_cases.yaml"

sys.path.insert(0, str(EXAMPLE_ROOT))
from mcp_servers.booking import server as booking_server
from mcp_servers.booking.mock_db import bookings as BOOKINGS
from mcp_servers.expense import server as expense_server
from mcp_servers.expense.mock_db import POLICY_LIMITS
from mcp_servers.search import server as search_server
from mcp_servers.search.mock_db import FLIGHTS, HOTELS

# Sentinel for arg values only knowable at execution time (e.g. the flight_id of
# "the cheapest flight just searched"). Task 3 references this constant.
RUNTIME = "<runtime>"

# Map server module -> agent-facing prefix derived from the server dir name.
_SERVERS = {
    "search": search_server,
    "booking": booking_server,
    "expense": expense_server,
}

# Expected tool-name coverage — guards against an introspection method that
# silently returns nothing.
_EXPECTED_TOOL_NAMES = {
    "wrangler_search_mcp_search_flights",
    "wrangler_search_mcp_search_hotels",
    "wrangler_booking_mcp_book_flight",
    "wrangler_booking_mcp_book_hotel",
    "wrangler_booking_mcp_cancel_booking",
    "wrangler_booking_mcp_get_booking_details",
    "wrangler_booking_mcp_list_all_bookings",
    "wrangler_expense_mcp_submit_expense",
    "wrangler_expense_mcp_check_expense_policy",
    "wrangler_expense_mcp_get_user_expenses",
}


def _build_tool_registry():
    """name -> (all_params: set, required_params: set), via FastMCP introspection.

    FastMCP 3.x: ``mcp.list_tools()`` is async and returns FunctionTool objects.
    Each exposes ``.name`` and ``.parameters`` (a JSON schema with ``properties``
    and ``required``). We map the bare func name to the agent-facing tool name
    ``wrangler_<server>_mcp_<func>`` using the server dir name.
    """
    registry: dict[str, tuple[set, set]] = {}
    for dirname, module in _SERVERS.items():
        tools = asyncio.run(module.mcp.list_tools())
        for tool in tools:
            schema = tool.parameters or {}
            all_params = set(schema.get("properties", {}).keys())
            required = set(schema.get("required", []))
            agent_name = f"wrangler_{dirname}_mcp_{tool.name}"
            registry[agent_name] = (all_params, required)
    return registry


# Build ground truth once at import time.
TOOL_REGISTRY = _build_tool_registry()
VALID_TOOL_NAMES = set(TOOL_REGISTRY)

# Mock-DB valid values (derived, not hardcoded).
FLIGHT_IDS = {f["id"] for f in FLIGHTS}
HOTEL_IDS = {h["id"] for h in HOTELS}
HOTEL_CITIES = {h["city"] for h in HOTELS}
AIRPORT_CODES = {f["origin"] for f in FLIGHTS} | {f["destination"] for f in FLIGHTS}
BOOKING_IDS = set(BOOKINGS.keys())
EXPENSE_CATEGORIES = set(POLICY_LIMITS.keys())
# User IDs: union of booking owners and (would-be) expense owners. The booking
# mock_db alone covers EMP001-EMP004; union with expense_db owners for safety.
from mcp_servers.expense.mock_db import expenses as _EXPENSES

USER_IDS = {b["user_id"] for b in BOOKINGS.values()} | {e["user_id"] for e in _EXPENSES.values()}

# Per-arg existence rules: arg name -> (valid set, label).
_EXISTENCE_RULES = {
    "flight_id": (FLIGHT_IDS, "flight ID"),
    "hotel_id": (HOTEL_IDS, "hotel ID"),
    "booking_id": (BOOKING_IDS, "seeded booking ID"),
    "user_id": (USER_IDS, "user ID"),
}


def _load_cases():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)["eval_cases"]


def test_introspected_registry_covers_all_expected_tools():
    """Sanity-check the introspection path before relying on it."""
    missing = _EXPECTED_TOOL_NAMES - VALID_TOOL_NAMES
    extra = VALID_TOOL_NAMES - _EXPECTED_TOOL_NAMES
    assert not missing and not extra, (
        f"Introspected tool registry does not match expected set.\n"
        f"  missing: {sorted(missing)}\n"
        f"  unexpected: {sorted(extra)}"
    )
    # list_all_bookings must derive to zero params.
    assert TOOL_REGISTRY["wrangler_booking_mcp_list_all_bookings"] == (set(), set())


def _case_label(idx, case):
    prompt = case.get("prompt", "<no prompt>")
    return f"[case #{idx}] {prompt[:70]!r}"


def test_eval_cases_structural_integrity():
    """Aggregate ALL structural violations across every case, then assert empty.

    Each violation is collected with the case label and the specific problem so
    the failure output enumerates every defect at once (anti-drift design).
    """
    cases = _load_cases()
    violations: list[str] = []

    for idx, case in enumerate(cases):
        label = _case_label(idx, case)
        category = case.get("category")
        is_error_handling = category == "error_handling"

        for tool in case.get("expected_tools", []):
            name = tool.get("name")
            args = tool.get("args", {}) or {}

            # Check 1: tool name is known.
            if name not in VALID_TOOL_NAMES:
                violations.append(
                    f"{label}: unknown tool name {name!r} (not in {sorted(VALID_TOOL_NAMES)})"
                )
                # Can't check args against an unknown signature.
                continue

            all_params, required = TOOL_REGISTRY[name]
            arg_keys = set(args.keys())

            # Check 2: no unknown arg keys.
            unknown = arg_keys - all_params
            if unknown:
                violations.append(
                    f"{label}: tool {name!r} has unknown arg key(s) "
                    f"{sorted(unknown)} (valid params: {sorted(all_params)})"
                )

            # Check 3: required params present (sentinel counts as present).
            missing_required = required - arg_keys
            if missing_required:
                violations.append(
                    f"{label}: tool {name!r} missing required param(s) "
                    f"{sorted(missing_required)} (args present: {sorted(arg_keys)})"
                )

            # Check 4: list_all_bookings must have empty args.
            if name == "wrangler_booking_mcp_list_all_bookings" and args != {}:
                violations.append(f"{label}: tool {name!r} must have empty args, got {args!r}")

            # Check 5: referenced-ID / enum existence.
            if is_error_handling:
                continue
            for arg_name, arg_value in args.items():
                if arg_value == RUNTIME:
                    continue  # runtime sentinel — value not knowable statically
                # Generic ID rules.
                if arg_name in _EXISTENCE_RULES:
                    valid_set, arg_label = _EXISTENCE_RULES[arg_name]
                    if arg_value not in valid_set:
                        violations.append(
                            f"{label}: tool {name!r} arg {arg_name}={arg_value!r} "
                            f"is not a valid {arg_label}"
                        )
                    continue
                # search_hotels city.
                if name == "wrangler_search_mcp_search_hotels" and arg_name == "city":
                    if arg_value not in HOTEL_CITIES:
                        violations.append(
                            f"{label}: tool {name!r} arg city={arg_value!r} "
                            f"is not a valid hotel city {sorted(HOTEL_CITIES)}"
                        )
                    continue
                # search_flights origin/destination airport codes.
                if name == "wrangler_search_mcp_search_flights" and arg_name in (
                    "origin",
                    "destination",
                ):
                    if arg_value not in AIRPORT_CODES:
                        violations.append(
                            f"{label}: tool {name!r} arg {arg_name}={arg_value!r} "
                            f"is not a valid airport code {sorted(AIRPORT_CODES)}"
                        )
                    continue
                # expense category enum (submit_expense / check_expense_policy).
                if arg_name == "category" and name in (
                    "wrangler_expense_mcp_submit_expense",
                    "wrangler_expense_mcp_check_expense_policy",
                ):
                    if str(arg_value).lower() not in EXPENSE_CATEGORIES:
                        violations.append(
                            f"{label}: tool {name!r} arg category={arg_value!r} "
                            f"is not a valid expense category {sorted(EXPENSE_CATEGORIES)}"
                        )
                    continue

    assert not violations, (
        f"{len(violations)} eval-case structural violation(s) found:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
