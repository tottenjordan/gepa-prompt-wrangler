"""Can we choose the judge that scores a batch eval? Probe, do not assume.

Two different models are called "the judge" in this repo and only one of them is ours:

    GEPA optimize stage   DEFAULT_JUDGE_MODEL = gemini-3.5-flash   chosen by A/B 2026-08-20
    Batch eval            the Vertex service DEFAULT AUTORATER     never set, never recorded

Every campaign number -- campaign 06's noise floor, campaign 07 and 08's criterion and
holdout deltas, the five-arm finding -- comes from the second. `evaluator.py` says so in a
comment (*"Leaving it unset uses the service default autorater"*) and nothing downstream
records which model that was on the day.

Whether it can be pinned is not documented anywhere we can find, and the surrounding
evidence points both ways, which is why this probes rather than concludes:

  - `vertexai.types` has no `AutoraterConfig`. It exists only in
    `vertexai.preview.evaluation` -- the legacy surface CLAUDE.md forbids mixing with
    `agentplatform`, having been bitten twice by exactly that on 2026-09-08.
  - `client.evals.create_evaluation_run()` takes no judge or autorater parameter.
  - The four metrics carrying our results are `types.RubricMetric.*(version="v1")`, which
    are `LazyLoadedPrebuiltMetric`: resolved server-side, exposing no `judge_model`. Their
    `__call__` does accept arbitrary kwargs into `metric_kwargs`, but CLAUDE.md records the
    predefined handler **ignoring** rubric prefills and `metric_spec_parameters`.
  - `types.LLMMetric` DOES expose `judge_model`, `judge_model_sampling_count` (1-32) and
    `judge_model_system_instruction`. CLAUDE.md rules out a **bare model id** only. A full
    resource name has never been tried.

**Types come from `agentplatform`, never `vertexai`.** The client isinstance-checks its
own types, so the two packages' objects have identical fields and different classes --
`model_dump()` shows no diff while the call fails. An earlier revision of this script
imported from `vertexai` and every variant failed, including its own no-config baseline,
which looked exactly like "the feature does not work". `tests/test_sdk_private_surface.py`
caught it.

**Ignored and rejected are different answers and the distinction is the point.** Rejected
means pinning is impossible on this surface. Ignored means the API accepts a value and
silently disregards it -- which is worse, because a future reader will set it and believe
they have.

Usage:
    uv run python scripts/probe_autorater_control.py --dry-run   # what it would try
    uv run python scripts/probe_autorater_control.py --capture outputs/captures/<file>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)


def _resource_name(model: str) -> str:
    """The full autorater path. A bare id is rejected; this is the form never tried."""
    from wrangler.core.config import GCP_PROJECT_ID
    from wrangler.core.models import model_location

    return (
        f"projects/{GCP_PROJECT_ID}/locations/{model_location(model)}"
        f"/publishers/google/models/{model}"
    )


def cases() -> list[tuple[str, str, dict]]:
    """(id, what it establishes, kwargs) for each metric variant to try."""
    from wrangler.core.models import DEFAULT_JUDGE_MODEL

    full = _resource_name(DEFAULT_JUDGE_MODEL)
    return [
        ("llm-default", "baseline: the custom metric as shipped, no judge pinned", {}),
        (
            "llm-bare-id",
            "confirms the documented rejection still holds",
            {"judge_model": DEFAULT_JUDGE_MODEL},
        ),
        (
            "llm-full-name",
            "THE UNTRIED PATH: full resource name on a custom LLMMetric",
            {"judge_model": full},
        ),
        (
            "llm-sampling-4",
            "does judge_model_sampling_count reach the service",
            {"judge_model_sampling_count": 4},
        ),
    ]


def build_llm_metric(**kwargs):
    """The shipped tool-use metric, plus whatever judge config we are testing."""
    from agentplatform import types

    from wrangler.eval.evaluator import _TOOL_USE_JUDGE_PROMPT, _TOOL_USE_METRIC_NAME

    return types.LLMMetric(
        name=_TOOL_USE_METRIC_NAME, prompt_template=_TOOL_USE_JUDGE_PROMPT, **kwargs
    )


def predefined_accepts_judge_model() -> str:
    """Does a predefined RubricMetric even carry a judge_model through construction?

    Offline: establishes whether the kwarg is stored, which is the precondition for it
    reaching the server. Storage is not honouring -- only a scored run distinguishes those.
    """
    from agentplatform import types

    metric = types.RubricMetric.SAFETY(version="v1", judge_model="probe")
    stored = getattr(metric, "metric_kwargs", {})
    if "judge_model" not in stored:
        return "DROPPED at construction -- cannot reach the server"
    return "stored in metric_kwargs -- reaches resolve(), honouring still unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", help="a capture to score; required unless --dry-run")
    ap.add_argument("--dry-run", action="store_true", help="construct only, make no calls")
    ap.add_argument(
        "--predefined-only",
        action="store_true",
        help="offline check of the predefined metrics. Runs alone because it poisons "
        "LazyLoadedPrebuiltMetric's class-level cache for the rest of the process.",
    )
    args = ap.parse_args()

    # The predefined check runs LAST and in its own invocation, never before the live
    # ones. LazyLoadedPrebuiltMetric keeps a CLASS-LEVEL cache keyed by name@version;
    # constructing a SAFETY metric with a bogus judge_model here poisoned it for every
    # scoring call later in the same process, and the probe's own baseline then failed.
    # That looked exactly like "the feature does not work". It was the harness.
    if args.predefined_only:
        print("PREDEFINED METRICS (the four that carry our results)")
        print(f"  RubricMetric.SAFETY(judge_model=...): {predefined_accepts_judge_model()}")
        return 0

    print("CUSTOM LLMMetric (tool_use_quality only)")
    for name, purpose, kwargs in cases():
        try:
            build_llm_metric(**kwargs)
            built = "constructs"
        except Exception as exc:
            print(f"  {name:16} REJECTED at construction: {type(exc).__name__}: {str(exc)[:90]}")
            continue
        if args.dry_run or not args.capture:
            print(f"  {name:16} {built}  -- {purpose}")
            continue
        try:
            from wrangler.eval.evaluator import score_captured

            result = score_captured(
                args.capture, metrics=[build_llm_metric(**kwargs)], agent_name=f"probe-{name}"
            )
            scores = {k: round(v, 4) for k, v in result.scores.items()}
            print(f"  {name:16} SCORED {scores}  cov={result.coverage}")
        except Exception as exc:
            print(f"  {name:16} FAILED at scoring: {type(exc).__name__}: {str(exc)[:110]}")

    if args.dry_run or not args.capture:
        print("\nConstruction only. Pass --capture to find out what the SERVICE does with it;")
        print("a value the API accepts and silently ignores looks identical here.")
        return 0

    print(
        "\nREAD BEFORE CONCLUDING:\n"
        "  - Identical scores across variants mean the judge config was IGNORED, not that\n"
        "    the models agree. Ignored and rejected are different answers.\n"
        "  - This probes the CUSTOM metric. The four predefined metrics that carry every\n"
        "    campaign result are resolved server-side and are not covered by a pass here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
