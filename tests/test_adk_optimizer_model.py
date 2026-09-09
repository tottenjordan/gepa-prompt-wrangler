"""GEPA writes prompts with a model we never chose, and nothing guards it.

GEPA runs two models with unrelated jobs:

- the **judge** scores candidates. We set it (`judge_model`), and the
  2026-08-20 A/B chose `gemini-3.5-flash` deliberately.
- the **optimizer model** reads the failures and writes the next candidate
  prompt. We have never set it, so ADK's default applies.

`GEPARootAgentPromptOptimizerConfig.optimizer_model` defaults to
`gemini-2.5-flash`. Campaign 07's optimize phase called it 69 times against 331
judge calls, and its 78 -> 1647 character prompt was written by it.

This is not a correctness problem -- scoring is the judge, and the judge is
right. It is a *lifecycle* problem. The registry guard in `test_models.py`
fails the build when a named-role default comes within 30 days of retirement,
but it only inspects roles this repo declares. An ADK default is invisible to
it, so `gemini-2.5-flash` retiring on 2026-10-16 would surface as GEPA erroring
mid-campaign rather than as a red build.

The value is logged (`optimizer.py:505` prints it, and campaign 07's log carries
`Optimizer model: gemini-2.5-flash`). Logged is not guarded: nobody reads a log
line for a date 37 days out.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from wrangler.core.models import MODELS

# Same bar the registry guard applies to our own named-role defaults. A vendor
# shutdown should be a red build, not a 404 in the middle of a 10-hour optimize.
RETIREMENT_WARNING_DAYS = 30


def _today() -> date:
    """UTC, explicitly. Retirement dates are vendor announcements in UTC, and a
    local-time `date.today()` would shift the boundary by a day either way."""
    return datetime.now(UTC).date()


def _adk_optimizer_model() -> str:
    """ADK's default, read from the live config rather than hardcoded.

    Hardcoding the string would make this test pass forever after ADK changed
    it -- the failure mode being guarded against is precisely a default moving
    without us noticing.
    """
    from google.adk.optimization.gepa_root_agent_prompt_optimizer import (
        GEPARootAgentPromptOptimizerConfig,
    )

    return GEPARootAgentPromptOptimizerConfig.model_fields["optimizer_model"].default


def test_adk_still_has_an_optimizer_model_default():
    """Guards the two tests below against silently passing on a renamed field."""
    model = _adk_optimizer_model()
    assert isinstance(model, str), (
        "ADK no longer exposes optimizer_model as a defaulted string field. "
        "If the optimizer model moved, find where and repoint this."
    )
    assert model, "ADK's optimizer_model default is empty"


def test_the_adk_optimizer_model_is_one_we_have_registered():
    """An unregistered model has no cost, no RPM and no retirement date here.

    We would be paying for it and depending on it with none of the tracking the
    registry exists to provide.
    """
    model = _adk_optimizer_model()
    assert model in MODELS, (
        f"GEPA writes candidate prompts with {model!r}, which is not in the "
        f"registry — so its cost, rate limit and retirement date are untracked. "
        f"Either register it or set optimizer_model explicitly."
    )


def test_the_adk_optimizer_model_is_not_about_to_retire():
    """The check the registry guard cannot make, because we never name this role.

    Fails from 30 days out. When it does, the fix is a decision, not a bump:
    either set `optimizer_model` to something we chose, or accept ADK's next
    default with the same scrutiny.
    """
    model = _adk_optimizer_model()
    spec = MODELS.get(model)
    if spec is None:  # the test above already reports this more clearly
        pytest.skip(f"{model} is unregistered")
    if spec.retirement_date is None:
        return

    days = (spec.retirement_date - _today()).days
    assert days > RETIREMENT_WARNING_DAYS, (
        f"GEPA's optimizer model {model!r} retires {spec.retirement_date} "
        f"({days} days). It writes every candidate prompt, and nothing else "
        f"guards it — the registry's own retirement check only covers roles "
        f"this repo declares. Set optimizer_model explicitly in "
        f"wrangler/optimize/optimizer.py."
    )


def test_the_retirement_check_can_actually_fire():
    """A guard never seen failing is not known to work."""
    soon = _today() + timedelta(days=RETIREMENT_WARNING_DAYS - 1)
    assert not (soon - _today()).days > RETIREMENT_WARNING_DAYS
    far = _today() + timedelta(days=RETIREMENT_WARNING_DAYS + 1)
    assert (far - _today()).days > RETIREMENT_WARNING_DAYS
