"""`use_merge=True` (patch 7) is injected but structurally inert. Pin why.

Patch 7 was shipped on the strength of a published claim -- GEPA+Merge gives prompts up to
9.2x shorter while scoring higher -- without checking whether our configuration can exhibit
it. It cannot: merge is *field-wise recombination across multiple predictors*, and
`GEPARootAgentPromptOptimizer` optimizes one (`agent_prompt`). The m01 stage logged 19 x
`No merge candidates found` and zero merges; `scripts/check_merge_eligibility.py` measured
34 pairs on a real run with 0 eligible.

These tests pin the upstream behaviour the "inert" conclusion rests on, so a gepa bump that
changes merge semantics -- e.g. to LLM-based blending of two prompts, which WOULD apply to a
single predictor -- fails the build instead of quietly making three documents wrong.
"""

from __future__ import annotations


def test_the_eligibility_function_still_exists_where_we_read_it():
    from gepa.proposer.merge import does_triplet_have_desirable_predictors  # noqa: F401


def test_a_single_predictor_triplet_is_never_eligible():
    """The load-bearing fact. One predictor, all three programs differing -> no merge.

    This is the real shape: an ancestor and two descendants, each of which mutated the one
    and only prompt. If this ever returns True, merge has become live for us and the
    `use_merge` notes in CLAUDE.md and the two analysis docs are wrong.
    """
    from gepa.proposer.merge import does_triplet_have_desirable_predictors as eligible

    candidates = [
        {"agent_prompt": "ancestor prompt"},
        {"agent_prompt": "child one prompt"},
        {"agent_prompt": "child two prompt"},
    ]
    assert eligible(candidates, 0, 1, 2) is False


def test_it_becomes_eligible_only_when_a_parent_matches_the_ancestor():
    """Shows the test is satisfiable, so the one above is not vacuously passing.

    A parent that left the predictor untouched is exactly what recombination needs -- and
    exactly what a mutation-driven lineage never produces for its only predictor.
    """
    from gepa.proposer.merge import does_triplet_have_desirable_predictors as eligible

    candidates = [
        {"agent_prompt": "ancestor prompt"},
        {"agent_prompt": "ancestor prompt"},  # unchanged from the ancestor
        {"agent_prompt": "child two prompt"},
    ]
    assert eligible(candidates, 0, 1, 2) is True


def test_two_predictors_are_eligible_when_each_parent_changed_a_different_one():
    """The configuration merge is designed for, and the condition that would revive it.

    If ADK ever optimizes sub-agent instructions alongside the root prompt, candidates gain
    predictors and `use_merge` stops being inert. That is the trigger to re-run
    scripts/check_merge_eligibility.py, and this test documents the shape to look for.
    """
    from gepa.proposer.merge import does_triplet_have_desirable_predictors as eligible

    candidates = [
        {"root": "R0", "sub": "S0"},
        {"root": "R1", "sub": "S0"},  # changed root, kept sub
        {"root": "R0", "sub": "S1"},  # kept root, changed sub
    ]
    assert eligible(candidates, 0, 1, 2) is True


def test_a_failed_merge_falls_through_rather_than_burning_the_iteration():
    """Why leaving the inert flag on is harmless rather than wasteful.

    The engine attempts merge first; when `propose()` returns None it continues to the
    reflective proposer in the SAME iteration, so no evaluation budget is spent. If this
    ordering changes, the cost/benefit of keeping patch 7 changes with it.
    """
    import inspect

    from gepa.core.engine import GEPAEngine

    source = inspect.getsource(GEPAEngine)
    merge_at = source.find("self.merge_proposer.propose(state)")
    reflective_at = source.find("self.reflective_proposer.propose(state)")
    moved = (
        "gepa's engine no longer calls both proposers by these names -- re-derive whether a "
        "failed merge still costs nothing before trusting the 'harmless' note on patch 7"
    )
    assert merge_at != -1, moved
    assert reflective_at != -1, moved
    assert merge_at < reflective_at, "merge is no longer attempted before reflective mutation"
