"""The tool-use judge prompt is a dated re-baseline. Pin which one is live.

Promoted 2026-09-17 after DOE 02 arm 4 measured, over five scoring passes each against one
capture with only the prompt varying:

                    original            hardened
  cases scored      312/320 (8 lost)    320/320 (0 lost)
  sd of the mean    0.0100              0.0043

Case loss eliminated, run-to-run variance more than halved, aggregate unmoved (+0.0056,
inside the original's own sd).

Both prompts stay in the tree. The original is not dead weight: it is the control that makes
the comparison reproducible, and the guard below is what stops the live one being swapped
back without the boundary being re-declared.
"""

from __future__ import annotations

from wrangler.eval.evaluator import (
    _TOOL_USE_JUDGE_PROMPT,
    _TOOL_USE_JUDGE_PROMPT_HARDENED,
    _tool_use_metric,
)


def test_the_hardened_prompt_is_the_live_one():
    assert _tool_use_metric().prompt_template == _TOOL_USE_JUDGE_PROMPT_HARDENED, (
        "the tool-use judge is back on the original prompt. That re-introduces ~2.5% case "
        "loss and doubles run-to-run variance -- and it moves the re-baseline boundary, so "
        "it needs a dated note, not just a revert."
    )


def test_the_original_is_kept_for_comparison():
    """Deleting it would make the measurement unreproducible and the boundary unauditable."""
    assert _TOOL_USE_JUDGE_PROMPT
    assert _TOOL_USE_JUDGE_PROMPT != _TOOL_USE_JUDGE_PROMPT_HARDENED


def test_only_the_output_contract_differs():
    """The claim 'the aggregate does not move' rests on the criteria being identical.

    If the rubric drifts between the two, the measured +0.0056 stops being attributable to
    the format and the whole arm-4 comparison is void.
    """
    marker = "# Output format"
    a = _TOOL_USE_JUDGE_PROMPT.split(marker)[0]
    b = _TOOL_USE_JUDGE_PROMPT_HARDENED.split(marker)[0]
    assert a == b, (
        "the criteria sections of the two tool-use prompts have diverged, so they no "
        "longer isolate output format and DOE 02 arm 4's comparison no longer holds"
    )


def test_the_hardened_prompt_keeps_its_format_rules():
    """The four changes that produced the result. Losing one silently loses the benefit."""
    p = _TOOL_USE_JUDGE_PROMPT_HARDENED
    assert '"score"' in p, "the hardened prompt no longer names a score field"
    assert p.index('"score"') < p.index('"explanation"'), (
        "score must come FIRST so a truncated response still carries the number"
    )
    for rule in ("no code fences", "NOT contain double quotes", "ONE short sentence"):
        assert rule.lower() in p.lower(), f"the hardened prompt lost its {rule!r} rule"


def test_both_prompts_keep_the_placeholders_the_service_fills():
    for p in (_TOOL_USE_JUDGE_PROMPT, _TOOL_USE_JUDGE_PROMPT_HARDENED):
        for key in ("{prompt}", "{response}", "{agent_data}"):
            assert key in p, f"missing {key}"
