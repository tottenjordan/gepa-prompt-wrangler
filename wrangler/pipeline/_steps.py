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
from typing import Any

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
