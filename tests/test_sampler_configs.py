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

_AGENTS = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / "agents"

# Deliberately-divergent criteria, with the reason and an expiry.
#
# Campaign 08 tests whether PR #69's instruction-adherence weighting (1 of 2 rubrics -> 3
# of 4) removes the regression campaign 07 reproduced on two model families. That needs
# both weightings running concurrently, and the criteria are chosen by agent-module name --
# so the baseline condition has to be a second directory carrying the *old* criteria.
#
# Named rather than pattern-matched, and exempted from two guards rather than the guards
# being relaxed, following ROLES_EXEMPT_FROM_REGISTRATION and PYTHON_MISMATCH_ACCEPTED.
# `test_the_baseline_is_exactly_the_pre_pr69_shape` pins what it is allowed to be, so the
# control condition cannot quietly become a second copy of the treatment.
#
# Delete this, the directory, and campaign 08's manifests together when 08 reports.
BASELINE_CONFIGS = {"sonnet_baseline_opt"}

# Every config, baseline included. The exemption is applied by the two tests it
# concerns, not here -- a baseline arm still has to name registered metrics, keep the
# train/validation split, and avoid vacuous rubrics. Dropping it from CONFIGS wholesale
# would exempt it from all nine guards to get relief from two.
CONFIGS = sorted(_AGENTS.glob("*_opt/sampler_config.json"))
TREATMENT_CONFIGS = [p for p in CONFIGS if p.parent.name not in BASELINE_CONFIGS]


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

    Campaign 08's baseline arm is excluded by name -- see BASELINE_CONFIGS. It exists
    precisely to carry different criteria, and it is pinned by its own test.
    """
    digests = {}
    for path in TREATMENT_CONFIGS:
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


# --- Instruction-following pressure (campaign 08 experiment) -------------------
#
# Campaign 07 reproduced a regression across two model families: GEPA improved
# `safety_v1`, a criterion it is scored on, and degraded `instruction_following_v1`,
# the one metric absent from these criteria. Both survived their noise floor *and*
# the run-to-run spread; nothing else did.
#
# The obvious response -- add `instruction_following_v1` to the criteria -- is not
# available. It is not in ADK's metric evaluator registry, and a sampler config naming
# it raises NotFoundError before a single candidate is scored. Verified against the
# installed ADK by the test below rather than taken from CLAUDE.md, which was already
# stale about a neighbouring entry.
#
# So the pressure goes through the registered rubric metric instead, and the
# experiment is a weighting change: instruction adherence went from 1 of 2 rubrics to
# 3 of 4. The threshold is deliberately left at 0.85 -- one variable at a time, or the
# result cannot be attributed.

INSTRUCTION_METRIC = "rubric_based_final_response_quality_v1"


def test_instruction_following_v1_is_not_a_criterion():
    """Naming it would break every optimize run before it scored anything.

    This is a guard against a plausible and tempting edit, not a hypothetical: it is
    the literal form of the campaign 08 change, and it fails at runtime rather than at
    review.
    """
    for path in CONFIGS:
        assert "instruction_following_v1" not in _criteria(path), (
            f"{path.parent.name} names instruction_following_v1, which is not in ADK's "
            "metric evaluator registry -- GEPA raises NotFoundError. Put the pressure in "
            f"{INSTRUCTION_METRIC}'s INSTRUCTION_ADHERENCE rubrics instead."
        )


def test_every_criterion_is_actually_registered_with_adk():
    """The registry is the authority, not the documentation.

    CLAUDE.md's registered/unregistered lists were correct about
    `instruction_following_v1` and wrong about `final_response_match_v2` at ADK 2.8.0,
    so this asks ADK directly.
    """
    from google.adk.evaluation.metric_evaluator_registry import (
        DEFAULT_METRIC_EVALUATOR_REGISTRY,
    )

    known = set(getattr(DEFAULT_METRIC_EVALUATOR_REGISTRY, "_registry", {}) or {})
    if not known:  # pragma: no cover - registry internals moved
        pytest.skip("could not read ADK's metric registry")
    for path in CONFIGS:
        unknown = set(_criteria(path)) - known
        assert not unknown, (
            f"{path.parent.name} names metric(s) ADK does not register: {sorted(unknown)}. "
            "GEPA raises NotFoundError on these."
        )


@pytest.mark.parametrize("path", TREATMENT_CONFIGS, ids=lambda p: p.parent.name)
def test_instruction_adherence_outweighs_the_rest_of_its_metric(path):
    """The point of the change: adherence must not be averaged away.

    With one adherence rubric against one completeness rubric, a candidate could trade
    adherence for completeness and hold the metric at its threshold -- which is a fair
    description of what campaign 07 measured.
    """
    rubrics = _criteria(path)[INSTRUCTION_METRIC]["rubrics"]
    adherence = [r for r in rubrics if r["type"] == "INSTRUCTION_ADHERENCE"]
    assert len(adherence) > len(rubrics) - len(adherence), (
        f"{path.parent.name}: instruction adherence is {len(adherence)}/{len(rubrics)} "
        "rubrics and no longer carries the majority of this metric."
    )


@pytest.mark.parametrize("name", sorted(BASELINE_CONFIGS))
def test_the_baseline_is_exactly_the_pre_pr69_shape(name):
    """Pin what the exempted config is allowed to be.

    An exemption with nothing behind it is a hole. Campaign 08 compares adherence at 1 of
    2 rubrics against 3 of 4; if the baseline drifted to 3 of 4 the campaign would compare
    a condition with itself and report "no effect", which is indistinguishable from a real
    null result and far more misleading.
    """
    path = _AGENTS / name / "sampler_config.json"
    rubrics = _criteria(path)["rubric_based_final_response_quality_v1"]["rubrics"]
    ids = [r["rubric_id"] for r in rubrics]
    assert ids == ["instruction_adherence", "completeness"], (
        f"{name} is no longer the pre-#69 baseline: {ids}"
    )


@pytest.mark.parametrize("name", sorted(BASELINE_CONFIGS))
def test_the_baseline_differs_from_its_treatment_only_in_those_rubrics(name):
    """One variable, or the campaign measures two things at once.

    Thresholds, judge model, the other three metrics and the train/validation split must
    all be byte-identical to the arm it is compared against.
    """
    treatment = _AGENTS / name.replace("_baseline", "") / "sampler_config.json"
    assert treatment.exists(), f"no treatment config to compare {name} against"

    added = {"user_stated_requirements", "brevity_does_not_override_instructions"}

    def _normalised(path):
        cfg = json.loads(path.read_text())
        cfg.pop("app_name")
        frq = cfg["eval_config"]["criteria"]["rubric_based_final_response_quality_v1"]
        frq["rubrics"] = [r for r in frq["rubrics"] if r["rubric_id"] not in added]
        return cfg

    assert _normalised(treatment) == _normalised(_AGENTS / name / "sampler_config.json"), (
        f"{name} differs from its treatment beyond the PR #69 rubrics -- campaign 08 would "
        "have two variables"
    )
