"""Guards on the GEPA sampler configs — the single source of truth for criteria.

Per CLAUDE.md a `sampler_config.json` is used verbatim when present; manifest
thresholds do not override it. So these files decide what GEPA optimizes
against, and a difference between two of them is a difference in the target,
not in the model.
"""

import hashlib
import json
from pathlib import Path

import pytest

CONFIGS = sorted(
    (Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / "agents").glob(
        "*_opt/sampler_config.json"
    )
)


def _criteria(path):
    return json.loads(path.read_text())["eval_config"]["criteria"]


def test_there_are_configs_to_check():
    assert len(CONFIGS) >= 5, f"only found {[c.parent.name for c in CONFIGS]}"


def test_every_agent_optimizes_against_identical_criteria():
    """Arms of a sweep must share a target, or the comparison is confounded.

    Until 2026-08-22 lite and pro carried safety 0.80 / hallucination 0.80 /
    response-quality 0.50 while the rest carried 0.95 / 0.95 / 0.85. The sweep
    published that day equalised seed and budget across arms and still compared
    a model searching against a 0.50 bar with one searching against 0.85.
    """
    digests = {}
    for path in CONFIGS:
        digest = hashlib.sha256(json.dumps(_criteria(path), sort_keys=True).encode()).hexdigest()[
            :12
        ]
        digests.setdefault(digest, []).append(path.parent.name)
    assert len(digests) == 1, f"criteria differ between agents: {digests}"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.parent.name)
def test_correct_parameters_rubric_is_not_vacuous(path):
    """A trajectory with no tool calls must fail this rubric, not pass it.

    "Accurate tool parameters provided." is scored 1.0 by both judges for a
    trajectory with zero tool calls — no parameters means no wrong parameters —
    which floors tool_use_quality at 0.5 for a completely non-functional agent.
    Same inverted incentive as silent-failures.md #4, one level down.
    """
    crit = _criteria(path).get("rubric_based_tool_use_quality_v1", {})
    rubric = next(
        (r for r in crit.get("rubrics", []) if r.get("rubric_id") == "correct_parameters"), None
    )
    assert rubric is not None, "correct_parameters rubric missing"
    text = rubric["rubric_content"]["text_property"].lower()
    assert "no tool was called" in text or "at least one tool" in text, (
        "the rubric must state what happens when no tool is called, or it is "
        f"vacuously true: {text!r}"
    )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.parent.name)
def test_rubrics_say_enough_to_judge_consistently(path):
    """Terse rubrics make the judge invent its own bar, which becomes variance.

    The originals were 4-6 words ("Correct tools selected."). Judge variance
    lands directly in the noise floor a sweep has to clear.
    """
    for name, spec in _criteria(path).items():
        for rubric in (spec.get("rubrics") or []) if isinstance(spec, dict) else []:
            text = rubric["rubric_content"]["text_property"]
            assert len(text.split()) >= 15, (
                f"{path.parent.name}/{name}/{rubric['rubric_id']} is {len(text.split())} "
                f"words: {text!r}"
            )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.parent.name)
def test_train_validation_split_is_preserved(path):
    """These are hand-tuned; a rubric edit must not disturb them."""
    d = json.loads(path.read_text())
    assert len(d["train_eval_case_ids"]) == 49
    assert len(d["validation_eval_case_ids"]) == 15
