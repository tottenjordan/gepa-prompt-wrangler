"""Freeze an engine's responses before deleting it, so the evidence outlives the engine.

**Why this exists.** Campaign 09's three engines were reaped on 2026-09-21, correctly and by
policy — they were labelled `lifecycle: ephemeral` and the project had reached 80 engines. On
2026-09-22 its control arm's drift turned out to be a factor of **44** larger between eval
sides than between two inference passes minutes apart, which is a state change rather than
noise. The one measurement that could say whether that change was permanent or transient — a
fresh capture from the same engine — was impossible, because the engines were gone.

That ordering is the general case, not bad luck: **engines are reaped on a schedule and
anomalies are investigated afterwards.** A campaign's write-up is published before anyone
knows which number will turn out to be interesting.

A final capture costs ~5 minutes and makes the loss survivable. The responses are frozen as a
canary — plain JSON, durable across SDK bumps — so they can be re-scored indefinitely against
any future judge, long after the engine that produced them is deleted.

**Targeted at campaign engines only.** An engine carrying a `campaign` label is one whose
numbers someone may have to defend; a scratch engine with no campaign is not worth five
minutes. Routine pruning stays fast.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Engines carrying this label belong to a campaign whose result may be re-examined.
CAMPAIGN_LABEL = "campaign"

#: Where frozen pre-reap captures land. Under `outputs/` because these are large and
#: per-engine; promote one into `data/canaries/` when a campaign actually depends on it.
FORENSICS_DIR = "outputs/forensics"


def needs_forensics(row: dict) -> bool:
    """True when this engine's responses are worth keeping past its deletion.

    A `campaign` label is the signal: it marks an engine whose numbers are in a write-up, and
    therefore one whose behaviour someone may need to re-measure. Scratch engines are not
    worth the five minutes, which is what keeps routine pruning fast.
    """
    return bool((row.get("labels") or {}).get(CAMPAIGN_LABEL))


def forensic_path(row: dict, out_dir: str | Path = FORENSICS_DIR) -> Path:
    """Where this engine's frozen capture goes.

    Named for the engine id as well as the display name: display names repeat across
    redeploys, ids do not, and the id is what a stage artifact records.
    """
    name = (row.get("display_name") or "engine").replace("/", "-")
    return Path(out_dir) / f"{name}_{row['id']}.json"


def capture_engine(
    row: dict,
    *,
    eval_data: str,
    out_dir: str | Path = FORENSICS_DIR,
    capture_fn: Any = None,
    freeze_fn: Any = None,
    load_fn: Any = None,
) -> dict:
    """Run one final inference pass against an engine and freeze it. Returns a record.

    **Never raises.** A capture that fails must be reported, not thrown — the caller decides
    whether that blocks the delete, and an exception here would abort a batch prune partway
    with some engines gone and no record of which.

    The `*_fn` seams exist so this is testable without an engine; production passes none.
    """
    from ..core.converter import load_eval_file
    from ..eval.canary import freeze_canary
    from ..eval.evaluator import capture_inference, load_capture

    capture_fn = capture_fn or capture_inference
    freeze_fn = freeze_fn or freeze_canary
    load_fn = load_fn or load_capture

    engine_id = row["id"]
    label = f"reap-{row.get('display_name') or engine_id}"
    record: dict[str, Any] = {
        "engine_id": engine_id,
        "display_name": row.get("display_name", ""),
        "campaign": (row.get("labels") or {}).get(CAMPAIGN_LABEL, ""),
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "path": "",
        "rows": 0,
        "error": "",
    }
    try:
        cases = load_eval_file(eval_data)
        pkl = capture_fn(
            engine_id=engine_id,
            eval_cases=cases,
            label=label,
            model=row.get("model", ""),
            agent_name=label,
        )
        frame = load_fn(pkl)
        dest = forensic_path(row, out_dir)
        record["path"] = freeze_fn(
            frame,
            dest,
            label=label,
            source=str(pkl),
            engine_id=engine_id,
            model=row.get("model", ""),
        )
        record["rows"] = len(frame)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def summarise(records: list[dict]) -> list[str]:
    """Printable lines for a prune run."""
    if not records:
        return []
    lines = [f"\n  Pre-reap captures ({len(records)}):"]
    for r in records:
        if r["error"]:
            lines.append(f"    {r['engine_id']}  FAILED — {r['error']}")
        else:
            lines.append(f"    {r['engine_id']}  {r['rows']} cases -> {r['path']}")
    return lines
