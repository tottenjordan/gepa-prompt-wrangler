"""A fixed set of responses, re-scored on every eval side, so autorater drift is measured.

**The problem this exists for.** Campaign 09's control arm drifted **+0.0732** on `safety_v1`
against the 0.0082 DOE 03 measured for it — 9x — and all three arms' safety rose together
between eval sides roughly 16 h apart. That is the shape of a service-side autorater change
rather than a prompt effect, and the campaign could not tell the two apart, so its primary
readout was reported UNRESOLVED.

**Why not just record which autorater ran.** Because we cannot.
`client.evals.create_evaluation_run()` takes no judge parameter, `vertexai.types` has no
`AutoraterConfig`, and the predefined metrics resolve server-side — CLAUDE.md records all
three. The autorater is not ours and is not reported. What *can* be done is measure its
behaviour: score the same bytes twice and read the difference.

**A canary is the same responses, scored again.** Freeze one inference pass; re-score it at
each eval side of a campaign. Its delta is the judge's drift over that window, with the agent
and the prompt held exactly constant, because they are not re-run at all. A campaign then has
its own floor for its own window instead of one borrowed from a DOE measured minutes apart.

**JSON, not pickle, and that is the whole point.** `save_capture` writes a pickle and says so
in its own docstring: *"scratch, not archive — an SDK bump can render an old one unloadable."*
A canary must outlive SDK bumps or it cannot compare across time, which is its only job. So a
canary is plain JSON, and the one field that is not JSON-native (`thought_signature`, an
opaque thinking-continuation token) is base64-encoded rather than dropped — "the same bytes"
has to be true for the reading to mean anything.

Cost is about 2.8 min per scoring pass on 64 cases, and it touches no engine, so it carries
none of the deployment lottery that makes `num_runs` expensive.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

#: Where a frozen canary lives by default. Checked in deliberately when a campaign depends on
#: one: a canary regenerated per run measures a different agent pass and answers nothing.
CANARY_DIR = "data/canaries"

#: Marks a base64-encoded `bytes` leaf. Self-describing so a reader does not need this module
#: to see what happened to the field.
_BYTES_TAG = "__bytes_b64__"

#: Rebuilt rather than stored: identical on every row, and the only non-plain object in the
#: frame. Storing it would make the canary depend on the SDK class it is meant to outlive.
_SESSION_USER = "wrangler-eval"


def _encode(obj: Any) -> Any:
    """Make a nested structure JSON-safe, preserving `bytes` exactly."""
    if isinstance(obj, bytes):
        return {_BYTES_TAG: base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj: Any) -> Any:
    """Inverse of `_encode`."""
    if isinstance(obj, dict):
        if set(obj) == {_BYTES_TAG}:
            return base64.b64decode(obj[_BYTES_TAG])
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def freeze_canary(
    frame: pd.DataFrame,
    out_path: str | Path,
    *,
    label: str,
    source: str = "",
    engine_id: str = "",
    model: str = "",
) -> str:
    """Write a durable JSON canary from an inference frame. Returns the path.

    `source`, `engine_id` and `model` are provenance, not inputs to scoring — a reading is
    only interpretable next to what produced the responses.
    """
    cols = [c for c in frame.columns if c != "session_inputs"]
    payload = {
        "canary_version": 1,
        "label": label,
        "frozen_at": datetime.now(tz=UTC).isoformat(),
        "source_capture": str(source),
        "engine_id": engine_id,
        "model": model,
        "rows": len(frame),
        "columns": cols,
        "cases": [_encode(rec) for rec in frame[cols].to_dict(orient="records")],
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return str(path)


def load_canary(path: str | Path) -> pd.DataFrame:
    """Rebuild the scorable frame from a frozen canary.

    `session_inputs` is reconstructed rather than read back: it is the one SDK object in the
    frame, identical on every row, and keeping it out of the file is what lets a canary
    survive the SDK bump that would strand a pickle.
    """
    import pandas as pd
    from agentplatform import types

    payload = json.loads(Path(path).read_text())
    cases = [_decode(rec) for rec in payload["cases"]]
    frame = pd.DataFrame(cases)
    frame["session_inputs"] = [
        types.evals.SessionInput(user_id=_SESSION_USER, state={}) for _ in range(len(frame))
    ]
    # Column order as frozen, with session_inputs restored to where _build_eval_dataset puts it.
    ordered = list(payload["columns"])
    ordered.insert(min(1, len(ordered)), "session_inputs")
    return frame[ordered]


def canary_metadata(path: str | Path) -> dict:
    """Provenance without loading or decoding the cases."""
    payload = json.loads(Path(path).read_text())
    return {k: v for k, v in payload.items() if k != "cases"}


def score_canary(
    path: str | Path,
    *,
    metrics: list | None = None,
    repeats: int = 1,
    tag: str = "canary",
) -> dict:
    """Score a frozen canary and return one reading.

    Makes **no agent calls** — the responses come out of the file, so this measures the judge
    and nothing else. That is the property the whole idea rests on.

    `repeats` averages several scoring passes, the same lever `score_repeats` pulls during a
    campaign; a reading taken at s=1 carries the judge's own per-pass noise and a drift
    smaller than that cannot be read.
    """
    from agentplatform import types

    from ..core.config import GCP_PROJECT_ID, GCP_REGION
    from .evaluator import DEFAULT_METRICS, _score_dataset, agent_client

    frame = load_canary(path)
    dataset = types.EvaluationDataset(eval_dataset_df=frame)
    client = agent_client(project=GCP_PROJECT_ID, location=GCP_REGION)

    passes: list[dict[str, float]] = []
    coverages: list[dict[str, int]] = []
    for i in range(max(1, repeats)):
        # agent_resource is None: a canary's engine may be long deleted, and engine ids are
        # never pinned in this repo. `_score_dataset` already treats it as optional.
        result = _score_dataset(
            client,
            dataset,
            metrics if metrics is not None else DEFAULT_METRICS,
            agent_resource=None,
            tag=f"{tag} {i + 1}/{repeats}" if repeats > 1 else tag,
        )
        passes.append(dict(result.scores))
        coverages.append(dict(result.coverage))

    keys = sorted({k for p in passes for k in p})
    scores = {
        k: sum(p[k] for p in passes if k in p) / max(1, sum(k in p for p in passes)) for k in keys
    }
    # Cases-per-metric, recorded because a mean is not comparable across differing
    # coverage -- that is the dropout silent-failures #5 showed reads as a real effect, and
    # it is exactly what a drift reading would otherwise mistake for the judge moving.
    # Measured on the first real run: a 2026-09-22 re-score of a 2026-09-17 capture came
    # back 61/64 on `instruction_following_v1` against 64/64 originally.
    coverage = {k: min((c[k] for c in coverages if k in c), default=0) for k in keys}
    return {
        "label": canary_metadata(path).get("label", ""),
        "canary_path": str(path),
        "scored_at": datetime.now(tz=UTC).isoformat(),
        "repeats": max(1, repeats),
        "rows": canary_metadata(path).get("rows", 0),
        "scores": scores,
        "coverage": coverage,
        "passes": passes,
    }


def canary_drift(before: dict, after: dict) -> dict:
    """Per-metric movement between two readings of the **same** canary.

    This is the number a campaign could not produce. The responses are identical by
    construction, so whatever moved is the judge -- and any campaign delta smaller than this
    is not a prompt effect, whatever the control arm says.

    Refuses to compare readings of different canaries: two different response sets scored at
    two different times measure nothing.
    """
    a, b = before.get("canary_path"), after.get("canary_path")
    if a and b and a != b:
        msg = f"readings are of different canaries ({a!r} vs {b!r}); the drift would be meaningless"
        raise ValueError(msg)

    s_before, s_after = before.get("scores", {}), after.get("scores", {})
    shared = sorted(set(s_before) & set(s_after))
    deltas = {k: s_after[k] - s_before[k] for k in shared}

    # A metric whose coverage moved is NOT a drift reading: the two means are over different
    # case sets, so the difference mixes dropout with the judge. Reported separately rather
    # than folded in, and excluded from max_abs_drift, which a campaign reads as a threshold.
    c_before, c_after = before.get("coverage", {}), after.get("coverage", {})
    uneven = sorted(
        k for k in shared if k in c_before and k in c_after and c_before[k] != c_after[k]
    )
    comparable = {k: v for k, v in deltas.items() if k not in uneven}
    return {
        "label": before.get("label", ""),
        "from": before.get("scored_at", ""),
        "to": after.get("scored_at", ""),
        "deltas": deltas,
        "uneven_coverage": uneven,
        "max_abs_drift": max((abs(v) for v in comparable.values()), default=0.0),
        # Named rather than silently dropped: a metric present on one side only usually means
        # the metric set changed between readings, which invalidates the comparison for it.
        "unmatched": sorted(set(s_before) ^ set(s_after)),
    }


def canary_reading_for_stage(
    canary_path: str,
    *,
    repeats: int = 1,
    root: str = "",
    tag: str = "canary",
) -> dict:
    """Score the configured canary for one eval side, for the stage artifact.

    **Never raises.** A canary is instrumentation: if it fails, the campaign it is measuring
    must still finish. A stage that died because its thermometer broke would be a worse
    outcome than an unmeasured window, so a failure is recorded as `{"error": ...}` and the
    eval carries on.

    Returns `{}` when no canary is configured, which is the default -- this is opt-in, so an
    existing manifest gets byte-identical behaviour and no extra spend.

    `root` prefixes the path for the pipeline container, where the code tarball is unpacked
    at `/app`.
    """
    if not canary_path:
        return {}

    resolved = str(Path(root) / canary_path) if root else canary_path
    try:
        reading = score_canary(resolved, repeats=repeats, tag=tag)
    except Exception as exc:
        print(f"  [{tag}] could not score canary {resolved}: {type(exc).__name__}: {exc}")
        return {"canary_path": resolved, "error": f"{type(exc).__name__}: {exc}", "scores": {}}
    print(f"  [{tag}] canary {reading['label']}: {len(reading['scores'])} metric(s) scored")
    return reading
