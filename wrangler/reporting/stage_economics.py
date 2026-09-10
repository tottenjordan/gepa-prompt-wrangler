"""Keep a run's per-stage cost and wall clock, instead of summing them away.

Both `results` builders — `pipeline/components.py` and `orchestration/stages.py` — load
the three stage artifacts and then collapse their token usage into one figure per arm.
That figure is correct and `_cost_benefit_section` needs it, but it cannot answer the more
useful question: *which stage*. On the real `c07-pro` run the two answers diverge:

    optimize   $0.328   30,742 s   87% of wall clock, 36% of dollars
    eval       $0.575    4,550 s   13% of wall clock, 64% of dollars

Eval is where the money goes; optimize is where the clock goes. At roughly $0.90 a run the
dollars barely matter and the binding constraints are wall clock and judge RPM — a campaign
is scheduled in hours, not budget. The summed total hides all of it.

**This lives here, not inlined in each caller, on purpose.** KFP's isolation rule is
narrower than it first appears: a component cannot call a module-level helper defined in
`components.py`, because only the function body is serialized. It *can* import from
`wrangler` freely, since every component extracts the tarball and does
`sys.path.insert(0, "/app")` first — `generate_analysis` already imports `generate_report`
this way. So one shared implementation is available to both callers, and the two builders
do not become a third hand-synced pair to guard.
"""

from __future__ import annotations

from typing import Any

# Chronological, so the rendered table follows the run rather than dict ordering.
STAGE_ORDER = ("eval_before", "optimize", "eval_after")


def _one(stage: dict[str, Any] | None) -> dict[str, Any]:
    """One stage's economics, or `{}` when the stage did not run.

    Absent and zero are different claims. An eval-only run has no optimize stage at
    all; rendering zeros for it would assert that optimization was free rather than
    that it never happened — the same distinction the existing cost code already draws
    between `n/a` and `$0.00`.
    """
    if not stage:
        return {}

    usage = stage.get("token_usage") or {}
    costs = stage.get("costs") or {}
    elapsed = stage.get("elapsed")

    if not usage and not costs and elapsed is None:
        return {}

    return {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        # Every token figure this repo records is an estimate. Carried through so the
        # renderer can say so; a dollar figure to four places that was never metered
        # reads as metering.
        "is_estimate": bool(usage.get("is_estimate", False)),
        "input_usd": costs.get("input_usd", 0.0) or 0.0,
        "output_usd": costs.get("output_usd", 0.0) or 0.0,
        "elapsed": elapsed or 0.0,
    }


def build_stage_usage(
    eval_before: dict[str, Any] | None,
    optimize: dict[str, Any] | None,
    eval_after: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Per-stage tokens, dollars and wall clock for one arm.

    Sits alongside the existing summed `token_usage` rather than replacing it: the
    summed value stays correct and its reader is unchanged. The two must reconcile —
    a report whose adjacent tables disagree is worse than one table.
    """
    return {
        "eval_before": _one(eval_before),
        "optimize": _one(optimize),
        "eval_after": _one(eval_after),
    }


def stage_totals(stage_usage: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Run-level sums across whichever stages actually ran."""
    present = [s for s in stage_usage.values() if s]
    return {
        "input_tokens": sum(s.get("input_tokens", 0) for s in present),
        "output_tokens": sum(s.get("output_tokens", 0) for s in present),
        "usd": sum(s.get("input_usd", 0.0) + s.get("output_usd", 0.0) for s in present),
        "elapsed": sum(s.get("elapsed", 0.0) for s in present),
    }
