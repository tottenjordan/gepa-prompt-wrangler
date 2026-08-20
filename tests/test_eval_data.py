"""Tests for eval data consistency — structure, signals, and mock data alignment."""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "multi_model_agents"
EVAL_DATA_DIR = EXAMPLE_ROOT / "eval_data"
AGENTS_DIR = EXAMPLE_ROOT / "agents"

sys.path.insert(0, str(EXAMPLE_ROOT))
# E402 below is deliberate: the example package is only importable after
# the sys.path insert above.
from mcp_servers.search.mock_db import FLIGHTS, HOTELS  # noqa: E402

YAML_PATH = EVAL_DATA_DIR / "eval_cases.yaml"
MODELS = ["lite", "flash", "pro", "sonnet", "opus"]

# All 6 evalset files: (dir_name, filename, expected_eval_set_id).
# The 5 standard model agents follow the {model}_eval_set naming. opus48_opt is
# a variant that reuses the opus agent + eval_set_id "opus_eval_set" with a
# distinct filename/app_name, so it cannot be derived from the MODELS pattern.
EVALSET_FILES = [
    (f"{model}_opt", f"{model}_eval_set.evalset.json", f"{model}_eval_set") for model in MODELS
] + [
    ("opus48_opt", "opus_eval_set.evalset.json", "opus_eval_set"),
]


def _load_yaml_cases():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)["eval_cases"]


# Import the generator's OWN case-building code path so the parity test derives
# expected tool_uses exactly as generate_evalsets.py does — no re-implementation
# of the YAML→JSON conversion. EXAMPLE_ROOT is already on sys.path above.
from scripts.generate_evalsets import (  # noqa: E402
    EVALSET_TARGETS as GEN_EVALSET_TARGETS,
)
from scripts.generate_evalsets import (  # noqa: E402
    build_eval_case as _gen_build_eval_case,
)
from scripts.generate_evalsets import (  # noqa: E402
    load_yaml_cases as _gen_load_yaml_cases,
)
from scripts.generate_evalsets import (  # noqa: E402
    select_cases as _gen_select_cases,
)

FLIGHT_ID_RE = re.compile(r"^FL\d{3}$")
HOTEL_ID_RE = re.compile(r"^HT\d{3}$")

ALL_FLIGHT_IDS = {f["id"] for f in FLIGHTS}
ALL_HOTEL_NAMES = {h["name"] for h in HOTELS}
ALL_HOTEL_CITIES = {h["city"].lower() for h in HOTELS}


# ---------------------------------------------------------------------------
# eval_cases.yaml
# ---------------------------------------------------------------------------
class TestEvalCasesYaml:
    def test_yaml_loads_successfully(self):
        cases = _load_yaml_cases()
        assert isinstance(cases, list)

    def test_all_cases_have_required_fields(self):
        for case in _load_yaml_cases():
            assert "prompt" in case, f"Missing 'prompt' in case: {case}"
            assert "expected_response" in case, (
                f"Missing 'expected_response' in: {case['prompt'][:50]}"
            )
            assert "expected_tools" in case, f"Missing 'expected_tools' in: {case['prompt'][:50]}"

    def test_expected_tools_have_name(self):
        for case in _load_yaml_cases():
            for tool in case["expected_tools"]:
                assert "name" in tool, f"Tool missing 'name' in case: {case['prompt'][:50]}"

    def test_case_count(self):
        cases = _load_yaml_cases()
        assert len(cases) == 64, f"Expected 64 cases, got {len(cases)}"

    def test_all_cases_have_tier(self):
        for case in _load_yaml_cases():
            assert "tier" in case and case["tier"] in {"low", "medium", "high"}, (
                f"Missing or invalid 'tier' in case: {case['prompt'][:50]}"
            )

    def test_all_cases_have_category(self):
        valid = {
            "search",
            "policy",
            "booking",
            "expense",
            "planning",
            "cancellation",
            "boundary",
            "error_handling",
        }
        for case in _load_yaml_cases():
            assert "category" in case and case["category"] in valid, (
                f"Missing or invalid 'category' in case: {case['prompt'][:50]}"
            )

    def test_tier_distribution(self):
        from collections import Counter

        counts = Counter(c["tier"] for c in _load_yaml_cases())
        assert counts["low"] == 33, f"Expected 33 low, got {counts['low']}"
        assert counts["medium"] == 22, f"Expected 22 medium, got {counts['medium']}"
        assert counts["high"] == 9, f"Expected 9 high, got {counts['high']}"


# ---------------------------------------------------------------------------
# tier_eval_cases.py
# ---------------------------------------------------------------------------
class TestTierEvalCases:
    @pytest.fixture(autouse=True)
    def _load(self):
        from eval_data.tier_eval_cases import (
            HIGH_COMPLEXITY_CASES,
            LOW_COMPLEXITY_CASES,
            MEDIUM_COMPLEXITY_CASES,
            TIER_EVAL_CASES,
        )

        self.low = LOW_COMPLEXITY_CASES
        self.medium = MEDIUM_COMPLEXITY_CASES
        self.high = HIGH_COMPLEXITY_CASES
        self.tiers = TIER_EVAL_CASES

    def test_all_tiers_present(self):
        assert set(self.tiers.keys()) == {"low", "medium", "high"}

    def test_cases_have_required_fields(self):
        required = {
            "prompt",
            "reference",
            "category",
            "expected_tool",
            "expected_signals",
            "description",
        }
        for tier_name, cases in self.tiers.items():
            for case in cases:
                missing = required - set(case.keys())
                assert not missing, (
                    f"Tier {tier_name} case missing {missing}: {case['prompt'][:50]}"
                )

    def test_flight_id_signals_exist_in_mock_data(self):
        for tier_name, cases in self.tiers.items():
            for case in cases:
                for signal in case["expected_signals"]:
                    if FLIGHT_ID_RE.match(signal):
                        assert signal in ALL_FLIGHT_IDS, (
                            f"Flight {signal} in tier {tier_name} case "
                            f"'{case['prompt'][:50]}' not in mock FLIGHTS"
                        )


# ---------------------------------------------------------------------------
# agent_eval_configs.py
# ---------------------------------------------------------------------------
class TestAgentEvalConfigs:
    @pytest.fixture(autouse=True)
    def _load(self):
        from eval_data.agent_eval_configs import (
            EXPENSE_EVAL_CASES,
            ROUTER_EVAL_CASES,
            STANDALONE_EVAL_CASES,
            TRAVEL_EVAL_CASES,
            get_eval_cases,
            get_metrics,
        )

        self.travel = TRAVEL_EVAL_CASES
        self.expense = EXPENSE_EVAL_CASES
        self.router = ROUTER_EVAL_CASES
        self.standalone = STANDALONE_EVAL_CASES
        self.get_eval_cases = get_eval_cases
        self.get_metrics = get_metrics

    def test_all_agent_case_lists_non_empty(self):
        assert len(self.travel) > 0
        assert len(self.expense) > 0
        assert len(self.router) > 0
        assert len(self.standalone) > 0

    def test_cases_have_required_fields(self):
        required = {
            "prompt",
            "reference",
            "category",
            "expected_tool",
            "expected_signals",
            "description",
        }
        for name, cases in [
            ("travel", self.travel),
            ("expense", self.expense),
            ("router", self.router),
        ]:
            for case in cases:
                missing = required - set(case.keys())
                assert not missing, f"{name} case missing {missing}: {case['prompt'][:50]}"

    def test_standalone_equals_travel_plus_expense(self):
        assert self.standalone == self.travel + self.expense

    def test_get_eval_cases_returns_for_all_agents(self):
        agents = [
            "coordinator_agent",
            "travel_agent",
            "expense_agent",
            "router_agent",
            "lite_agent",
            "flash_agent",
            "pro_agent",
            "sonnet_agent",
            "opus_agent",
        ]
        for agent in agents:
            cases = self.get_eval_cases(agent)
            assert len(cases) > 0, f"No cases for {agent}"

    def test_get_metrics_returns_six(self):
        metrics = self.get_metrics("flash_agent")
        assert len(metrics) == 6


# ---------------------------------------------------------------------------
# evalset.json files
# ---------------------------------------------------------------------------
class TestEvalsetJsonFiles:
    def test_all_six_evalsets_exist(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            assert path.exists(), f"Missing evalset: {path}"

    def test_evalsets_valid_json(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            assert isinstance(data, dict)

    def test_evalsets_have_correct_structure(self):
        for dir_name, filename, expected_id in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            assert "eval_set_id" in data, f"{dir_name}: missing eval_set_id"
            assert "eval_cases" in data, f"{dir_name}: missing eval_cases"
            assert data["eval_set_id"] == expected_id, (
                f"{dir_name}: eval_set_id {data['eval_set_id']!r} != {expected_id!r}"
            )

    def test_evalsets_have_64_cases(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            assert len(data["eval_cases"]) == 64, (
                f"{dir_name}: expected 64 cases, got {len(data['eval_cases'])}"
            )

    def test_evalset_cases_have_conversation(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            for case in data["eval_cases"]:
                assert "eval_id" in case, f"{dir_name}: case missing eval_id"
                assert "conversation" in case, f"{dir_name}: case missing conversation"
                assert "session_input" in case, f"{dir_name}: case missing session_input"

    def test_evalset_session_input_has_category(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            for case in data["eval_cases"]:
                si = case["session_input"]
                assert "category" in si, (
                    f"{dir_name}: {case['eval_id']} missing category in session_input"
                )
                assert "tier" in si, f"{dir_name}: {case['eval_id']} missing tier in session_input"

    def test_evalset_session_input_app_name_matches_dir(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            for case in data["eval_cases"]:
                assert case["session_input"]["app_name"] == dir_name, (
                    f"{dir_name}: {case['eval_id']} app_name "
                    f"{case['session_input']['app_name']!r} != {dir_name!r}"
                )

    def test_evalset_eval_ids_include_category(self):
        for dir_name, filename, _ in EVALSET_FILES:
            path = AGENTS_DIR / dir_name / filename
            with open(path) as f:
                data = json.load(f)
            for case in data["eval_cases"]:
                parts = case["eval_id"].split("_", 3)
                assert len(parts) >= 3, f"{dir_name}: eval_id '{case['eval_id']}' missing tier"


# ---------------------------------------------------------------------------
# Mock data ↔ eval case consistency
# ---------------------------------------------------------------------------
class TestMockDataConsistency:
    def test_flight_ids_referenced_in_evals_exist(self):
        from eval_data.tier_eval_cases import TIER_EVAL_CASES

        for tier_name, cases in TIER_EVAL_CASES.items():
            for case in cases:
                for signal in case["expected_signals"]:
                    if FLIGHT_ID_RE.fullmatch(signal):
                        assert signal in ALL_FLIGHT_IDS, (
                            f"Flight {signal} in tier {tier_name} case "
                            f"'{case['prompt'][:50]}' not in mock FLIGHTS"
                        )

    def test_hotel_names_referenced_in_evals_exist(self):
        from eval_data.tier_eval_cases import TIER_EVAL_CASES

        for tier_name, cases in TIER_EVAL_CASES.items():
            for case in cases:
                for signal in case["expected_signals"]:
                    if any(signal in name for name in ALL_HOTEL_NAMES):
                        continue
                    if signal.startswith(
                        (
                            "Grand",
                            "Budget",
                            "Fontainebleau",
                            "Palmer",
                            "Ritz",
                            "Claridge",
                            "Park Hotel",
                            "Crawford",
                            "Liberty",
                        )
                    ):
                        assert any(signal in name for name in ALL_HOTEL_NAMES), (
                            f"Hotel signal '{signal}' in tier {tier_name} not in mock HOTELS"
                        )

    def test_cities_referenced_in_hotel_evals_have_data(self):
        from eval_data.agent_eval_configs import TRAVEL_EVAL_CASES

        for case in TRAVEL_EVAL_CASES:
            if case["expected_tool"] == "search_mcp_search_hotels":
                for signal in case["expected_signals"]:
                    if signal.lower() in ALL_HOTEL_CITIES:
                        break
                else:
                    if case["category"] != "edge_case":
                        city_signals = [s for s in case["expected_signals"] if s[0].isupper()]
                        for city in city_signals:
                            if city.lower() in ALL_HOTEL_CITIES:
                                break


# ---------------------------------------------------------------------------
# YAML ↔ committed evalset.json parity (drift guard)
# ---------------------------------------------------------------------------
class TestYamlEvalsetParity:
    """Fail if a committed *.evalset.json drifts from eval_cases.yaml.

    "Edited the YAML but forgot to re-run generate_evalsets.py" is the silent
    drift this branch exists to prevent. For each evalset target we rebuild the
    expected cases using the generator's OWN build_eval_case() (not a copy of
    the conversion), then assert the committed JSON's per-case tool_uses
    (tool name + args) match exactly. Network-free and deterministic.
    """

    def _expected_evalset(self, target):
        """Derive the expected eval_cases list for a target via the generator."""
        all_cases = _gen_load_yaml_cases()
        selected = _gen_select_cases(all_cases)
        return [
            _gen_build_eval_case(idx, tier, case, target["app_name"])
            for idx, (tier, case) in enumerate(selected, start=1)
        ]

    @staticmethod
    def _tool_uses(eval_case):
        """Pull the (name, args) tool_uses list out of an eval case dict."""
        conv = eval_case["conversation"][0]
        return conv["intermediate_data"]["tool_uses"]

    def test_targets_match_test_file_list(self):
        """The generator's EVALSET_TARGETS and this test file's EVALSET_FILES
        must describe the same six evalsets — otherwise a target could drift
        unguarded."""
        gen = {(t["dir_name"], t["filename"]) for t in GEN_EVALSET_TARGETS}
        local = {(d, f) for d, f, _ in EVALSET_FILES}
        assert gen == local, f"target list mismatch: {gen ^ local}"

    def test_committed_json_tool_uses_match_yaml(self):
        for target in GEN_EVALSET_TARGETS:
            path = AGENTS_DIR / target["dir_name"] / target["filename"]
            with open(path) as f:
                committed = json.load(f)
            expected_cases = self._expected_evalset(target)

            committed_cases = committed["eval_cases"]
            assert len(committed_cases) == len(expected_cases), (
                f"{target['dir_name']}: committed has {len(committed_cases)} cases, "
                f"YAML derives {len(expected_cases)} — regenerate evalsets"
            )

            for exp, got in zip(expected_cases, committed_cases, strict=False):
                assert got["eval_id"] == exp["eval_id"], (
                    f"{target['dir_name']}: eval_id drift "
                    f"{got['eval_id']!r} != {exp['eval_id']!r} — regenerate evalsets"
                )
                exp_tools = self._tool_uses(exp)
                got_tools = self._tool_uses(got)
                assert got_tools == exp_tools, (
                    f"{target['dir_name']}/{exp['eval_id']}: tool_uses drift from "
                    f"eval_cases.yaml.\n  committed: {got_tools}\n  from YAML : {exp_tools}\n"
                    "Run scripts/generate_evalsets.py to regenerate."
                )
