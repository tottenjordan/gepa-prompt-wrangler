"""Shared fixtures for gepa-prompt-wrangler tests."""

import pytest


@pytest.fixture
def sample_scores_before():
    return {
        "final_response_quality_v1": 0.65,
        "hallucination_v1": 0.70,
        "safety_v1": 0.60,
        "tool_use_quality_v1": 0.50,
        "instruction_following_v1": 0.40,
    }


@pytest.fixture
def sample_scores_after():
    return {
        "final_response_quality_v1": 0.90,
        "hallucination_v1": 0.85,
        "safety_v1": 1.00,
        "tool_use_quality_v1": 0.55,
        "instruction_following_v1": 0.75,
    }


@pytest.fixture
def sample_all_results(sample_scores_before, sample_scores_after):
    models = {
        "lite": "gemini-3.1-flash-lite",
        "flash": "gemini-3.5-flash",
        "pro": "gemini-3.1-pro-preview",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
    }
    results = {}
    for name, model in models.items():
        results[name] = {
            "model": model,
            "engine_id": f"{name}_engine_123",
            "original_prompt": "You are a helpful assistant.",
            "optimized_prompt": f"You are a specialized {name} agent with tool guidance and policy knowledge.",
            "before": dict(sample_scores_before),
            "after": dict(sample_scores_after),
        }
    return results


@pytest.fixture
def sample_eval_cases():
    return [
        {
            "prompt": "Find flights from SFO to JFK",
            "reference": "United FL001 from SFO to JFK at $450.",
            "expected_tool": "wrangler_search_mcp_search_flights",
        },
        {
            "prompt": "Search for hotels in New York",
            "reference": "Grand Hyatt at $320/night.",
            "expected_tool": "wrangler_search_mcp_search_hotels",
        },
        {
            "prompt": "Submit a $45 meals expense for EMP001",
            "expected_response": "Expense submitted and approved.",
            "expected_tool": "wrangler_expense_mcp_submit_expense",
        },
    ]


@pytest.fixture
def manifest_dict():
    return {
        "name": "test-run",
        "description": "Test manifest",
        "agent_module": "agents/test_agent",
        "eval_data": "eval_data/test.yaml",
        "pairs": [
            {
                "id": "gemini-flash",
                "model": "gemini-3.5-flash",
                "system_prompt": "You are a test assistant.",
            },
            {
                "id": "claude-sonnet",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a test assistant.",
            },
        ],
        "eval_config": {
            "judge_model": "gemini-2.5-pro",
            "response_match_threshold": 0.5,
        },
    }


@pytest.fixture
def sample_case_metadata():
    return [
        {"tier": "low", "category": "search", "prompt": "Find flights from SFO to JFK"},
        {"tier": "low", "category": "search", "prompt": "Search hotels in Chicago"},
        {"tier": "medium", "category": "policy", "prompt": "Check expense policy for meals"},
        {"tier": "medium", "category": "expense", "prompt": "Submit $200 lodging expense"},
        {"tier": "high", "category": "planning", "prompt": "Plan a 3-city trip with budget"},
        {"tier": "high", "category": "planning", "prompt": "Compare flight+hotel packages"},
    ]


@pytest.fixture
def sample_per_case_scores():
    return [
        {"final_response_quality_v1": 0.90, "safety_v1": 1.0, "tool_use_quality_v1": 0.80},
        {"final_response_quality_v1": 0.85, "safety_v1": 0.95, "tool_use_quality_v1": 0.75},
        {"final_response_quality_v1": 0.70, "safety_v1": 1.0, "tool_use_quality_v1": 0.60},
        {"final_response_quality_v1": 0.75, "safety_v1": 0.90, "tool_use_quality_v1": 0.65},
        {"final_response_quality_v1": 0.60, "safety_v1": 0.85, "tool_use_quality_v1": 0.50},
        {"final_response_quality_v1": 0.55, "safety_v1": 0.80, "tool_use_quality_v1": 0.45},
    ]


@pytest.fixture
def prompt_module_dir(tmp_path):
    def _create(agent_name="test"):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "__init__.py").write_text("")
        (prompts_dir / f"{agent_name}_prompts.py").write_text(
            'GENERIC = "You are a helpful assistant."\nOPTIMIZED = {}\nACTIVE = GENERIC\n'
        )
        return str(prompts_dir)

    return _create
