"""Read a noise-floor campaign's arms and produce the table its write-up needs.

`floor_from_control_arm` and `drift_sign_summary` existed but nothing called
them, so the first clean floor this project measured was still computed by
pasting Python into a terminal. That is how CLAUDE.md ended up carrying two
floor figures nobody can re-derive. One command over a campaign's run ids now
produces the whole table, so the number that lands in the docs has a
reproduction attached to it.

The GCS fetch is deliberately thin -- it maps run ids to artifacts and does
nothing else -- so every piece of arithmetic stays testable without a bucket.
"""

from __future__ import annotations

from .analyzer import drift_sign_summary, floor_from_control_arm

# How far the two sides of one arm may differ in coverage before its floor
# stops describing noise and starts describing dropout. Historical arms swung
# 42 points (sonnet 47% -> 89%); pooling one of those would measure the wrong
# thing entirely.
COVERAGE_GAP_LIMIT = 0.10


def summarize_arms(arms: dict[str, tuple[dict, dict]]) -> dict:
    """Per-arm floors, a pooled per-metric floor, and the drift sign test.

    ``arms`` maps an arm label to its ``(eval_before, eval_after)`` stage
    artifacts. Every arm is assumed to be a control -- byte-identical prompt on
    both sides -- which is what makes its movement a floor rather than a result.

    The pooled floor takes the **largest** movement per metric across arms, not
    the mean. Two arms disagreeing is information about how variable the floor
    itself is, and averaging it away would understate the noise -- the one
    direction that manufactures false wins.
    """
    per_arm: dict[str, dict] = {}
    warnings: list[str] = []

    for label, (before, after) in arms.items():
        floor = floor_from_control_arm(before, after)
        cov = floor["coverage"]
        gap = (
            abs(cov["before"] - cov["after"])
            if cov["before"] is not None and cov["after"] is not None
            else None
        )
        flagged = gap is not None and gap > COVERAGE_GAP_LIMIT
        if flagged:
            warnings.append(
                f"{label}: coverage differs by {gap:.0%} between sides "
                f"({cov['before']:.0%} vs {cov['after']:.0%}) — this arm's delta is "
                f"largely dropout and should not be pooled"
            )
        per_arm[label] = {**floor, "coverage_gap": gap, "coverage_warning": flagged}

    # Pool only the arms whose two sides are comparable. An arm measuring
    # dropout would dominate the max and quietly become "the floor".
    poolable = {k: v for k, v in per_arm.items() if not v["coverage_warning"]}
    pooled: dict[str, float] = {}
    for arm in poolable.values():
        for metric, delta in arm["unpaired"].items():
            pooled[metric] = max(pooled.get(metric, 0.0), abs(delta))

    return {
        "arms": per_arm,
        "pooled": pooled,
        "floor": max(pooled.values(), default=None),
        "drift": drift_sign_summary({k: v["unpaired"] for k, v in per_arm.items()}),
        "warnings": warnings,
    }


def render_markdown(summary: dict) -> str:
    """The Result section for docs/doe/06-pipeline-noise-floor.md."""
    out: list[str] = []
    arms = summary["arms"]

    out.append("### Per-arm floors\n")
    out.append("| arm | coverage before/after | n paired | scalar floor |")
    out.append("| --- | --- | --- | --- |")
    for label, a in arms.items():
        cov = a["coverage"]
        cb = f"{cov['before']:.0%}" if cov["before"] is not None else "?"
        ca = f"{cov['after']:.0%}" if cov["after"] is not None else "?"
        flag = " ⚠" if a["coverage_warning"] else ""
        fl = f"{a['floor']:.4f}" if a["floor"] is not None else "n/a"
        out.append(f"| {label}{flag} | {cb} / {ca} | {a['n_paired']} | {fl} |")
    out.append("")

    out.append("### Per-metric, unpaired and paired\n")
    out.append("Both, always. They disagree substantially, and reporting only the")
    out.append("smaller flatters the pipeline while only the larger hides a real lever.\n")
    out.append("| arm | metric | unpaired Δ | paired Δ |")
    out.append("| --- | --- | --- | --- |")
    for label, a in arms.items():
        for metric in sorted(a["unpaired"], key=lambda m: -abs(a["unpaired"][m])):
            paired = a["paired"].get(metric)
            ps = f"{paired:+.4f}" if paired is not None else "n/a"
            out.append(f"| {label} | {metric} | {a['unpaired'][metric]:+.4f} | {ps} |")
    out.append("")

    if summary["pooled"]:
        out.append("### Pooled floor, worst movement per metric\n")
        out.append("| metric | floor |")
        out.append("| --- | --- |")
        for metric, val in sorted(summary["pooled"].items(), key=lambda kv: -kv[1]):
            out.append(f"| {metric} | {val:.4f} |")
        out.append("")
        out.append(f"**Headline floor: {summary['floor']:.4f}** (worst pooled metric).\n")

    d = summary["drift"]
    out.append("### Drift sign test\n")
    out.append(
        f"{d['arms_negative']}/{d['arms_total']} arms drifted negative overall; "
        f"consistent = **{d['consistent']}**.\n"
    )
    out.append(
        "The five metrics are not independent — all score the same responses via the "
        "same autorater — so the **arms** are the unit. With four arms, unanimity is "
        "12.5% two-sided: suggestive, not conclusive.\n"
    )

    if summary["warnings"]:
        out.append("### ⚠ Warnings\n")
        out.extend(f"- {w}" for w in summary["warnings"])
        out.append("")

    out.append(
        "**On √n:** with only two `num_runs` levels this is a two-point comparison, not a "
        "fitted curve. Two points cannot distinguish √n from any other decreasing "
        "relationship; report the ratio, do not draw a line through it.\n"
    )
    return "\n".join(out)


def fetch_arms(run_ids: list[str], bucket_name: str) -> dict[str, tuple[dict, dict]]:
    """Pull each run's control-arm eval artifacts out of GCS.

    Pair ids are discovered by listing the `eval_before` prefix rather than
    being passed in: a campaign's run ids are printed by the runner, but its
    pair ids live in manifests, and requiring both is how a reader gets one
    wrong. An arm missing either side is skipped with a warning rather than
    silently half-counted.
    """
    import json as _json

    from google.cloud import storage

    bucket = storage.Client().bucket(bucket_name)
    arms: dict[str, tuple[dict, dict]] = {}

    for run_id in run_ids:
        prefix = f"pipeline-runs/{run_id}/stages/eval_before/"
        for blob in bucket.list_blobs(prefix=prefix):
            if not blob.name.endswith(".json"):
                continue
            pair_id = blob.name.rsplit("/", 1)[-1][: -len(".json")]
            after_blob = bucket.blob(f"pipeline-runs/{run_id}/stages/eval_after/{pair_id}.json")
            if not after_blob.exists():
                print(f"  skipping {pair_id}: no eval_after artifact (run {run_id})")
                continue
            arms[pair_id] = (
                _json.loads(blob.download_as_text()),
                _json.loads(after_blob.download_as_text()),
            )
    return arms
