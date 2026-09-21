"""Logic extracted from KFP component bodies, so it can be tested.

`components.py` holds the code that runs a campaign — and at 642 statements it sat at **2%
coverage**, because a `@dsl.component` body is serialised in isolation and cannot be called
from a test. Every defect found in that path surfaced hours or weeks late.

**Importing this module from a component is safe, and is already the established pattern.**
KFP's isolation rule is narrower than it first reads: a component cannot call a module-level
helper defined *in components.py*, because only the function body is serialised. It can
import from `wrangler` freely — every component extracts the tarball and calls
`sys.path.insert(0, "/app")` first, and they already do so 16 times. Checked 2026-09-01 and
recorded in CLAUDE.md.

Moving logic here also **shrinks what KFP serialises**, which reduces the surface that
silent-failures #13 — a component body serialised under the wrong name because KFP locates
it by import-time line number — can act on.

**What belongs here:** pure functions over plain data. **What does not:** anything touching
process lifecycle (MCP server startup, the `_deferred_toolset_closes` window, tarball
extraction). Those are why the component exists.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

#: Every engine this project creates carries it, and `wrangler engines prune` refuses to
#: delete anything without it — an engine that loses it becomes unreapable.
OWNERSHIP_LABEL = {"solution": "promp-wrangler"}


def build_engine_labels(engine_labels_json: str) -> dict[str, str]:
    """Merge a manifest's engine labels **under** the ownership label.

    The caller's labels are applied first and the ownership label last, so a manifest cannot
    overwrite it. That ordering is load-bearing: `prune` protects anything unlabelled on the
    grounds it might be someone else's live work, so an engine whose `solution` label was
    clobbered by a typo would survive forever with nothing to explain why.

    Malformed input degrades to the default rather than raising. A bad label string is not a
    reason to fail a deploy nine hours into a campaign, and the ownership label alone still
    leaves the engine reapable by traffic age.

    Values are coerced to `str`: GCP label values are strings, and a YAML `campaign: 09`
    arrives as an int.
    """
    extra: dict[str, Any] = {}
    if engine_labels_json:
        try:
            parsed = json.loads(engine_labels_json)
            if isinstance(parsed, dict):
                extra = parsed
        except (TypeError, ValueError):
            extra = {}
    return {**{str(k): str(v) for k, v in extra.items()}, **OWNERSHIP_LABEL}


def deploy_stage_payload(
    *,
    pair_id: str,
    engine_id: str,
    model: str,
    original_prompt: str,
    source: str,
    elapsed: float,
    health: dict | None,
) -> dict:
    """The deploy stage's GCS artifact.

    `health` is carried verbatim and never summarised to a boolean. `health.passed: false`
    with the eval running anyway is a finding, and the reader needs the rate and the rejected
    engine ids to see it — campaign 01 measured a redeploy moving an engine 0% → 50%, so the
    draw matters as much as the verdict.

    `original_prompt` is what the analyzer compares against the optimized one to decide
    whether an arm is a control (`PairAnalysis.is_control`), so it is recorded even when the
    engine was reused rather than freshly deployed.
    """
    return {
        "pair_id": pair_id,
        "engine_id": engine_id,
        "model": model,
        "original_prompt": original_prompt,
        "source": source,
        "elapsed": elapsed,
        "health": health,
    }


def redeploy_inputs(*, deploy_data: dict, optimize_data: dict, pair_model: str) -> dict[str, Any]:
    """Resolve what the redeploy stage should push, from the two upstream artifacts.

    **The manifest's model wins over the deploy record.** Two campaign 07 arms pointing at
    the same agent module both optimized whatever `config.py` pinned, so the frontier the
    campaign measured would have differed only by label. The pair's model is authoritative
    whenever it is set; the deploy record is the fallback for callers that never set one.

    `prompt_changed` is reported rather than inferred downstream because a control arm
    legitimately redeploys an identical prompt, and a report that implied work had happened
    would misdescribe the arm that exists to measure no work at all.
    """
    optimized = optimize_data.get("optimized_prompt", "")
    original = deploy_data.get("original_prompt", "")
    return {
        "engine_id": deploy_data["engine_id"],
        "model": pair_model or deploy_data.get("model", ""),
        "original_prompt": original,
        "optimized_prompt": optimized,
        "prompt_changed": optimized != original,
    }


def redeploy_stage_payload(*, pair_id: str, engine_id: str, elapsed: float, health: dict) -> dict:
    """The redeploy stage's GCS artifact.

    Timestamped in UTC with an offset, not naive: reports parse this field, and a naive
    stamp read against a differently-zoned one silently shifts a campaign's timeline.

    `health` is carried verbatim for the same reason as the deploy payload — an in-place
    update **redraws** the reach lottery (campaign 01 measured 0%→50% and 6%→56%), so the
    after-side draw is not the one `eval_before` was gated onto, and the verdict has to
    survive into the artifact.
    """
    from datetime import UTC, datetime

    return {
        "pair_id": pair_id,
        "engine_id": engine_id,
        "updated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "elapsed": elapsed,
        "health": health,
    }


def resolve_agent_paths(agent_module_path: Path) -> dict[str, Any]:
    """Find the `*_opt` package GEPA should optimize, and the root to put on `sys.path`.

    The optimizer loads `<stem>_opt/__init__.py` when one exists and falls back to the plain
    agent module otherwise. **A bare directory is not enough** — without `__init__.py` it is
    not importable, and treating it as a package fails inside GEPA rather than here.

    `project_root` is two levels up because `config.py` and `registry.py` live alongside
    `agents/`, not inside it, and the agent imports them by name.
    """
    from pathlib import Path

    agent_path = Path(agent_module_path)
    stem = agent_path.stem.replace("_agent", "")
    opt_dir = agent_path.parent / f"{stem}_opt"
    if opt_dir.is_dir() and (opt_dir / "__init__.py").exists():
        agent_path = opt_dir
    return {
        "agent_path": agent_path,
        "project_root": Path(agent_module_path).parent.parent,
        "sampler_config": agent_path / "sampler_config.json",
    }


def control_arm_payload(*, original_prompt: str) -> dict:
    """The optimize-stage artifact for an arm that runs no optimize stage.

    The prompt comes back **byte-identical**, not merely equivalent: `PairAnalysis.is_control`
    detects a control arm by `original_prompt == optimized_prompt`, and redeploy plus
    eval_after then run against the same prompt, which is what makes the arm measure the
    noise floor rather than a prompt change.

    Costs are explicit zeros rather than omitted, so a report summing across arms does not
    have to special-case a missing key.
    """
    return {
        "optimized_prompt": original_prompt,
        "elapsed": 0.0,
        "original_chars": len(original_prompt),
        "optimized_chars": len(original_prompt),
        "thresholds": {},
        "control_arm": True,
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "is_estimate": True},
        "costs": {"input_usd": 0.0, "output_usd": 0.0},
    }


#: Tokens-per-character heuristics for the optimize stage's cost estimate. GEPA does not
#: expose a metered count, so these stand in -- and every consumer must carry
#: `is_estimate` so the number is never mistaken for billing data.
_INPUT_TOKENS_PER_CHAR = 50
_OUTPUT_TOKENS_PER_CHAR = 10


def optimize_cost_summary(
    *, original_prompt: str, optimized_prompt: str, judge_costs: dict
) -> dict:
    """Estimated judge spend for one optimize stage.

    **An estimate, and it says so.** GEPA reports no metered token count, so this scales the
    two prompt lengths by fixed factors. `is_estimate` travels with the numbers because a
    cost that looks metered gets quoted as though it were.

    An unknown model yields zero rather than raising: a missing cost-table entry must not
    fail a nine-hour stage at the reporting step, after the expensive work is done.
    """
    est_input = len(original_prompt) * _INPUT_TOKENS_PER_CHAR
    est_output = len(optimized_prompt) * _OUTPUT_TOKENS_PER_CHAR
    return {
        "input_tokens": est_input,
        "output_tokens": est_output,
        "is_estimate": True,
        "input_usd": est_input * judge_costs.get("input", 0) / 1_000_000,
        "output_usd": est_output * judge_costs.get("output", 0) / 1_000_000,
    }
