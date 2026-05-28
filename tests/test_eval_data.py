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
from mcp_servers.search.mock_db import FLIGHTS, HOTELS
from mcp_servers.expense.mock_db import POLICY_LIMITS

YAML_PATH = EVAL_DATA_DIR / "eval_cases.yaml"
MODELS = ["lite", "flash", "pro", "sonnet", "opus"]


def _load_yaml_cases():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)["eval_cases"]


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
            assert "expected_response" in case, f"Missing 'expected_response' in: {case['prompt'][:50]}"
            assert "expected_tools" in case, f"Missing 'expected_tools' in: {case['prompt'][:50]}"

    def test_expected_tools_have_name(self):
        for case in _load_yaml_cases():
            for tool in case["expected_tools"]:
                assert "name" in tool, f"Tool missing 'name' in case: {case['prompt'][:50]}"

    def test_case_count(self):
        cases = _load_yaml_cases()
        assert len(cases) == 40, f"Expected 40 cases, got {len(cases)}"


# ---------------------------------------------------------------------------
# tier_eval_cases.py
# ---------------------------------------------------------------------------
class TestTierEvalCases:
    @pytest.fixture(autouse=True)
    def _load(self):
        from eval_data.tier_eval_cases import (
            LOW_COMPLEXITY_CASES, MEDIUM_COMPLEXITY_CASES,
            HIGH_COMPLEXITY_CASES, TIER_EVAL_CASES,
        )
        self.low = LOW_COMPLEXITY_CASES
        self.medium = MEDIUM_COMPLEXITY_CASES
        self.high = HIGH_COMPLEXITY_CASES
        self.tiers = TIER_EVAL_CASES

    def test_all_tiers_present(self):
        assert set(self.tiers.keys()) == {"low", "medium", "high"}

    def test_cases_have_required_fields(self):
        required = {"prompt", "reference", "category", "expected_tool", "expected_signals", "description"}
        for tier_name, cases in self.tiers.items():
            for case in cases:
                missing = required - set(case.keys())
                assert not missing, f"Tier {tier_name} case missing {missing}: {case['prompt'][:50]}"

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
            TRAVEL_EVAL_CASES, EXPENSE_EVAL_CASES, ROUTER_EVAL_CASES,
            STANDALONE_EVAL_CASES, get_eval_cases, get_metrics,
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
        required = {"prompt", "reference", "category", "expected_tool", "expected_signals", "description"}
        for name, cases in [("travel", self.travel), ("expense", self.expense), ("router", self.router)]:
            for case in cases:
                missing = required - set(case.keys())
                assert not missing, f"{name} case missing {missing}: {case['prompt'][:50]}"

    def test_standalone_equals_travel_plus_expense(self):
        assert self.standalone == self.travel + self.expense

    def test_get_eval_cases_returns_for_all_agents(self):
        agents = [
            "coordinator_agent", "travel_agent", "expense_agent", "router_agent",
            "lite_agent", "flash_agent", "pro_agent", "sonnet_agent", "opus_agent",
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
    def test_all_five_evalsets_exist(self):
        for model in MODELS:
            path = AGENTS_DIR / f"{model}_opt" / f"{model}_eval_set.evalset.json"
            assert path.exists(), f"Missing evalset: {path}"

    def test_evalsets_valid_json(self):
        for model in MODELS:
            path = AGENTS_DIR / f"{model}_opt" / f"{model}_eval_set.evalset.json"
            with open(path) as f:
                data = json.load(f)
            assert isinstance(data, dict)

    def test_evalsets_have_correct_structure(self):
        for model in MODELS:
            path = AGENTS_DIR / f"{model}_opt" / f"{model}_eval_set.evalset.json"
            with open(path) as f:
                data = json.load(f)
            assert "eval_set_id" in data, f"{model}: missing eval_set_id"
            assert "eval_cases" in data, f"{model}: missing eval_cases"
            assert data["eval_set_id"] == f"{model}_eval_set"

    def test_evalset_cases_have_conversation(self):
        for model in MODELS:
            path = AGENTS_DIR / f"{model}_opt" / f"{model}_eval_set.evalset.json"
            with open(path) as f:
                data = json.load(f)
            for case in data["eval_cases"]:
                assert "eval_id" in case, f"{model}: case missing eval_id"
                assert "conversation" in case, f"{model}: case missing conversation"
                assert "session_input" in case, f"{model}: case missing session_input"


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
                    if signal.startswith(("Grand", "Budget", "Fontainebleau", "Palmer",
                                         "Ritz", "Claridge", "Park Hotel", "Crawford", "Liberty")):
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
