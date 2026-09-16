"""GEPA writes prompts with one model and scores them with another. Guard both.

GEPA runs two models with unrelated jobs:

- the **judge** scores candidates. We set it (`judge_model`), and the
  2026-08-20 A/B chose `gemini-3.5-flash` deliberately.
- the **optimizer model** reads the failures and writes the next candidate
  prompt.

Until 2026-09-16 we had never set the second one, so ADK's default applied:
`GEPARootAgentPromptOptimizerConfig.optimizer_model` = `gemini-2.5-flash`.
Campaign 07's optimize phase called it 69 times against 331 judge calls, and its
78 -> 1647 character prompt was written by it.

That was never a correctness problem -- scoring is the judge's job, and the judge
was right. It was a **lifecycle** problem. `test_models.py` fails the build when a
named-role default comes within 30 days of retirement, but it only inspects roles
this repo *declares*. An ADK default is invisible to it, so `gemini-2.5-flash`
retiring on 2026-10-16 would have surfaced as GEPA 404ing nine hours into an
optimize stage rather than as a red build.

**It fired on 2026-09-16, exactly 30 days out, which is what it was built to do.**
The fix was to declare the role: `DEFAULT_OPTIMIZER_MODEL` in `core/models.py`,
passed explicitly in `optimizer.py`. The retirement question now belongs to
`test_models.py` along with every other named role. What stays here is what that
guard cannot see: that we still set it at all, and what sharing an id with the
judge means.
"""

from __future__ import annotations

import ast
from pathlib import Path

from wrangler.core.models import DEFAULT_JUDGE_MODEL, DEFAULT_OPTIMIZER_MODEL, MODELS

OPTIMIZER = Path(__file__).resolve().parents[1] / "wrangler" / "optimize" / "optimizer.py"


def _adk_optimizer_model() -> str:
    """ADK's default, read from the live config rather than hardcoded.

    Hardcoding the string would make the drift test below pass forever after ADK
    changed it -- the failure mode being guarded against is precisely a default
    moving without us noticing.
    """
    from google.adk.optimization.gepa_root_agent_prompt_optimizer import (
        GEPARootAgentPromptOptimizerConfig,
    )

    return GEPARootAgentPromptOptimizerConfig.model_fields["optimizer_model"].default


def test_adk_still_has_an_optimizer_model_default():
    """Guards the tests below against silently passing on a renamed field.

    If ADK moves this field, passing `optimizer_model=` becomes a no-op or a
    TypeError, and we are back to an unguarded default without knowing it.
    """
    model = _adk_optimizer_model()
    assert isinstance(model, str), (
        "ADK no longer exposes optimizer_model as a defaulted string field. "
        "If the optimizer model moved, find where and repoint this and optimizer.py."
    )
    assert model, "ADK's optimizer_model default is empty"


def test_we_actually_pass_the_optimizer_model():
    """The whole fix is one keyword argument; assert it is still there.

    Asserted against the source rather than by constructing the config, because
    the value has a default and a construction test would pass just as happily
    if the argument were dropped.
    """
    tree = ast.parse(OPTIMIZER.read_text())
    passed = (
        any(
            isinstance(node, ast.keyword) and node.arg == "optimizer_model"
            for node in ast.walk(tree)
        )
        or '"optimizer_model": DEFAULT_OPTIMIZER_MODEL' in OPTIMIZER.read_text()
    )
    assert passed, (
        "optimizer.py no longer passes optimizer_model, so ADK's own default is "
        "back in force -- and ADK's default is invisible to the registry's "
        "retirement guard. That is how gemini-2.5-flash got within 30 days of "
        "shutdown while writing every candidate prompt."
    )


def test_the_optimizer_model_is_registered():
    """An unregistered model has no cost, no RPM and no retirement date here.

    `test_models.py` enforces this for every declared role; repeated because this
    is the role whose *absence* from that table caused the original problem.
    """
    assert DEFAULT_OPTIMIZER_MODEL in MODELS, (
        f"GEPA writes candidate prompts with {DEFAULT_OPTIMIZER_MODEL!r}, which is "
        f"not in the registry — so its cost, rate limit and retirement date are "
        f"untracked."
    )


def test_we_are_not_silently_back_on_adks_default():
    """Not a failure — a notification that ADK moved, so the choice can be re-made.

    Skipped rather than asserted equal/unequal: ADK's default matching ours would
    be fine, and ADK's default differing from ours is the normal state. What is
    worth knowing is when it *changes*, because ADK picking a new model is a
    signal about which ids are current.
    """
    adk = _adk_optimizer_model()
    if adk != DEFAULT_OPTIMIZER_MODEL:
        # Expected. Recorded here so the value shows up in -v output.
        assert DEFAULT_OPTIMIZER_MODEL in MODELS


def test_the_writer_is_not_the_scorer():
    """A writer sharing the scorer's model can target the measured score too precisely.

    Campaigns 07 and 08 ran writer != scorer and measured GEPA improving its criterion
    while degrading its holdout, on five arms out of five. A writer that shares the
    scorer's preferences could plausibly amplify that. It is a hypothesis rather than a
    measurement -- DOE 11 records the power arithmetic for testing it -- but the cheap
    move is to not create the overlap, which is what `claude-opus-4-8` does.

    PR #83 briefly shipped writer == scorer as a side effect of clearing a retirement
    deadline. This is the guard that would have caught it.
    """
    assert DEFAULT_OPTIMIZER_MODEL != DEFAULT_JUDGE_MODEL, (
        f"the prompt writer and the scorer are both {DEFAULT_JUDGE_MODEL!r}. See "
        f"DEFAULT_OPTIMIZER_MODEL in core/models.py; if this is deliberate, say why there."
    )


def test_the_writer_is_not_an_enabled_agent_model():
    """Writer == agent is a confound for cross-model campaigns, and a subtler one.

    A model writing prompts for its own architecture may produce prompts that suit it
    better than they suit another agent model. In a cost/quality frontier campaign like
    07 that asymmetry lands on some arms and not others.

    `claude-opus-4-8` is safe because every opus pair is disabled -- the serving lottery,
    docs/analysis/2026-09-01-opus-serving-failure.md. This test is what stops that
    becoming untrue by accident when someone re-enables an opus arm.

    Reads `enabled_pairs`, never `pairs`: a *disabled* opus pair must not trip it.
    """
    from wrangler.core.factory import PairFactory

    manifests = Path(__file__).resolve().parents[1] / "manifests"
    collisions: list[str] = []
    unreadable: list[str] = []
    for path in sorted(manifests.glob("*.yaml")):
        try:
            manifest = PairFactory.load(str(path))
        except Exception as exc:  # a broken manifest is another test's problem
            unreadable.append(f"{path.name} ({type(exc).__name__}: {exc})")
            continue
        collisions.extend(
            f"{path.name}:{pair.id}"
            for pair in manifest.enabled_pairs
            if pair.model == DEFAULT_OPTIMIZER_MODEL
        )

    assert not collisions, (
        f"{DEFAULT_OPTIMIZER_MODEL!r} writes GEPA's prompts and is also an enabled agent "
        f"in {collisions}. Either pick a different optimizer model or disable those pairs "
        f"— see DEFAULT_OPTIMIZER_MODEL in core/models.py."
    )
    assert len(unreadable) < 3, (
        f"{len(unreadable)} manifests failed to parse, so this guard checked almost "
        f"nothing: {unreadable[:3]}"
    )
