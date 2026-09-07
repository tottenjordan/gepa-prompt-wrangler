"""The eval path reaches into private google-genai internals. Pin them.

`wrangler/eval/evaluator.py` monkey-patches two private attributes of
`vertexai._genai._evals_common` and constructs a private `_gcs_utils.GcsUtils`.
Private means the SDK owes us no stability, so a routine dependency bump can
rename or remove any of them.

Nothing caught that. Before this file, `_evals_common` appeared in `tests/`
only inside a docstring, so a bump that removed `AGENT_MAX_WORKERS` would have
passed lint, passed the whole suite, and failed at run time with an
`AttributeError` partway through an eval — after a deploy, a health gate and
however much of a campaign had already been paid for.

These assertions are deliberately shallow. They do not test SDK behaviour; they
test that the four things this repo binds to still exist and still take the
arguments it passes. That is the whole job: turn a silent runtime break into a
red build on the dependabot PR that causes it.
"""

from __future__ import annotations

import inspect

import pytest


def test_evals_common_is_importable():
    """The module itself is private and has moved before."""
    from vertexai._genai import _evals_common

    assert _evals_common is not None


def test_agent_max_workers_exists_and_is_an_int():
    """`_run_batched_inference` saves, overwrites and restores this."""
    from vertexai._genai import _evals_common

    assert hasattr(_evals_common, "AGENT_MAX_WORKERS"), (
        "wrangler/eval/evaluator.py overwrites _evals_common.AGENT_MAX_WORKERS "
        "to throttle inference concurrency; it is gone from this SDK version"
    )
    assert isinstance(_evals_common.AGENT_MAX_WORKERS, int)


def test_the_retry_function_exists():
    from vertexai._genai import _evals_common

    assert callable(getattr(_evals_common, "_execute_agent_run_with_retry", None)), (
        "evaluator.py wraps _execute_agent_run_with_retry to raise the retry "
        "budget to EVAL_MAX_RETRIES; it is gone from this SDK version"
    )


def test_the_retry_function_still_accepts_max_retries():
    """The patch calls the original with `max_retries=`, so the name matters.

    Coverage is 1-(1-reach)^attempts, and at the 25% single-attempt reach
    measured on a degraded engine the difference between the SDK default and
    EVAL_MAX_RETRIES=16 is roughly 87% versus 99% of cases scored. If this
    keyword is renamed the wrapper raises TypeError on the first case; if it
    were silently swallowed by **kwargs the retry budget would revert with no
    error at all, which is worse.
    """
    from vertexai._genai import _evals_common

    sig = inspect.signature(_evals_common._execute_agent_run_with_retry)
    accepts = "max_retries" in sig.parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert accepts, (
        f"_execute_agent_run_with_retry no longer takes `max_retries`; "
        f"signature is {sig}. evaluator.py's EVAL_MAX_RETRIES patch would "
        f"raise TypeError on the first eval case."
    )


def test_gcs_utils_takes_an_api_client():
    """`_gcs_utils.GcsUtils(api_client=client._api_client)` is built by hand."""
    from vertexai._genai import _gcs_utils

    assert hasattr(_gcs_utils, "GcsUtils")
    sig = inspect.signature(_gcs_utils.GcsUtils)
    assert "api_client" in sig.parameters, (
        f"GcsUtils no longer takes `api_client`; signature is {sig}"
    )


@pytest.mark.parametrize(
    "name",
    ["AGENT_MAX_WORKERS", "_execute_agent_run_with_retry"],
)
def test_evaluator_still_binds_what_it_claims(name: str):
    """Keeps this file honest if the evaluator stops using one of these.

    A guard that pins a surface nobody depends on any more is noise, and the
    next person deletes it rather than reading it.
    """
    from pathlib import Path

    src = Path("wrangler/eval/evaluator.py").read_text()
    assert name in src, (
        f"{name} is pinned here but evaluator.py no longer references it — "
        f"drop the assertion instead of carrying a guard for nothing"
    )
