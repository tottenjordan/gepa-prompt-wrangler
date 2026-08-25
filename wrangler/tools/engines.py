"""Inventory and reap Agent Engines, by evidence rather than by age or name.

80 engines accumulated here unnoticed, the oldest from 2026-02-13. That is a
direct consequence of a rule worth keeping: CLAUDE.md forbids pinning engine
ids, so nothing in the repo names them — and nothing reaped them either.

**The count is not the dangerous part.** Only 48 of the 80 carried
``solution=promp-wrangler``, and three of the *unlabelled* ones were the busiest
engines in the project (8,576 / 3,101 / 2,397 requests over 30 days). A sweep by
age, or by matching a name prefix, would have deleted live work belonging to
somebody else.

So an engine is deletable only when **every** signal agrees, and each signal has
a veto:

===============  ==========================================================
ownership        must be labelled ``solution=promp-wrangler`` (or be on the
                 explicit, dated legacy list below)
traffic          no ``POST /api/stream_reasoning_engine`` in the window
reference        its id appears in no ``.env``, manifest or experiment config
warmth           not (``min_instances`` > 0 **and** referenced) — warm plus
                 referenced means someone is holding it hot deliberately
===============  ==========================================================

Only the warmth rule is conditional, and deliberately so: the probe engines are
warm and unreferenced, and reaping those is the point.

Usage:
    uv run wrangler engines list
    uv run wrangler engines prune          # dry run
    uv run wrangler engines prune --yes
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..core.config import GCP_PROJECT_ID, GCP_REGION

OWNER_LABEL = ("solution", "promp-wrangler")

# Engines that predate the labelling convention and are ours on the evidence of
# their naming, which matches the labelled set from the same era exactly.
#
# This list is hand-written and every entry carries its reasoning, because
# deleting an unlabelled engine is the one place this module overrides its own
# safety rule. Extending it is a human decision and a visible diff — never a
# pattern match. Anything that merely *looks* like ours and is not here stays.
LEGACY_UNLABELLED = {
    "8685308979372359680": (
        "wrangler-lite-agent, 2026-05-29: this repo's model-based naming from that "
        "era, matching the labelled 2026-05-22 set; idle and unreferenced"
    ),
    "6112627692236963840": (
        "wrangler-pro-agent, 2026-05-29: same cluster and naming as the above; "
        "idle and unreferenced"
    ),
    "1374840884243202048": (
        "sonnet-claude-4, 2026-05-29: duplicate of the labelled 2026-05-22 engine "
        "of the same name; idle and unreferenced"
    ),
    "4549878621539401728": (
        "opus-claude-4, 2026-05-29: duplicate of the labelled 2026-05-22 engine of "
        "the same name; idle and unreferenced"
    ),
    "4703001008869998592": (
        "flash-gemini-3.5-flash, 2026-05-29: duplicate of the labelled 2026-05-22 "
        "engine of the same name; idle and unreferenced"
    ),
}

# Deliberately absent: sonnet_agent (8467456143491334144, 2026-05-21). Its
# underscore naming matches the geap-tour set (sonnet_agent_jt1, opus_agent_jt1),
# so on the evidence it is theirs. Looking similar is not evidence.

# Probe engines deployed before the lifecycle label existed. Named explicitly
# for the same reason as LEGACY_UNLABELLED: an override of a safety rule should
# be a visible diff, not a pattern match. Everything deployed after this carries
# `lifecycle: ephemeral` and needs no entry here.
_PROBE_2X2 = "2x2 empty-stream probe, 2026-08-23; campaign written up in docs/analysis"
_PROBE_C01 = "Campaign 01 lottery probe, 2026-08-24; campaign written up in docs/doe"
EPHEMERAL_PRE_LABEL = {
    "3191356139119837184": _PROBE_2X2,
    "8437205280076333056": _PROBE_2X2,
    "554498557294411776": _PROBE_2X2,
    "1373309264545710080": _PROBE_2X2,
    "923793726738792448": _PROBE_C01,
    "4728209511960018944": _PROBE_C01,
    "8555143295318097920": _PROBE_C01,
    "6346690628046290944": _PROBE_C01,
    "4040847618832596992": _PROBE_C01,
    "5725756829422583808": _PROBE_C01,
    "3482401265038655488": _PROBE_C01,
    "890016729533513728": _PROBE_C01,
    "12377752149688320": _PROBE_C01,
    "642881699981557760": _PROBE_C01,
}

DEFAULT_WINDOW_DAYS = 30

# Where an engine id can legitimately be named. Result files under outputs/ are
# excluded on purpose: a bare number match there picked up 728 false hits.
DEFAULT_REFERENCE_PATHS = (
    ".env",
    "examples/multi_model_agents/.env",
    "manifests",
    "experiments",
)

# Matches an id only when it is the value of an engine-id key.
_ENGINE_ID_REF = re.compile(
    r"""(?:ENGINE_ID|engine_id)["']?\s*[:=]\s*["']?(\d{15,20})""",
)


def classify(engine: dict, traffic: int = 0, referenced: bool = False) -> dict:
    """Decide whether one engine may be deleted, and say what protected it.

    Returns the engine dict with ``deletable`` and ``reason`` added. ``reason``
    always names the *first* protecting signal, so a review reads as a list of
    justifications rather than a list of booleans.
    """
    out = dict(engine)
    labels = engine.get("labels") or {}
    key, value = OWNER_LABEL
    is_ours = labels.get(key) == value
    is_legacy = engine["id"] in LEGACY_UNLABELLED
    # An engine that declares itself scratch waives the traffic veto, because
    # the only traffic it ever sees is the probe traffic we sent to measure it.
    # The first run of this policy kept all 14 probe engines on 100-244
    # self-generated requests apiece.
    is_ephemeral = (
        labels.get("lifecycle") == "ephemeral" or engine["id"] in EPHEMERAL_PRE_LABEL
    ) and is_ours

    if not is_ours and not is_legacy:
        owner = labels.get(key)
        detail = f"labelled solution={owner}" if owner else "no ownership label"
        out.update(deletable=False, reason=f"not ours to delete — {detail}")
    elif traffic > 0 and not is_ephemeral:
        out.update(deletable=False, reason=f"traffic in window ({traffic} requests)")
    elif referenced and (engine.get("min_instances") or 0) > 0:
        out.update(deletable=False, reason="referenced and kept warm deliberately")
    elif referenced:
        out.update(deletable=False, reason="referenced in .env / manifest / experiment")
    else:
        if is_ephemeral:
            why, tail = "ephemeral", "campaign complete, unreferenced"
        elif is_legacy and not is_ours:
            why, tail = "legacy list", "no traffic, unreferenced"
        else:
            why, tail = "labelled ours", "no traffic, unreferenced"
        out.update(deletable=True, reason=f"{why}, {tail}")
    return out


def plan_prune(
    rows: list[dict],
    traffic: dict[str, int],
    referenced: set[str],
) -> dict:
    """Split an inventory into delete and keep, with a reason on every row."""
    delete, keep = [], []
    for row in rows:
        out = classify(row, traffic=traffic.get(row["id"], 0), referenced=row["id"] in referenced)
        (delete if out["deletable"] else keep).append(out)
    return {
        "delete": delete,
        "keep": keep,
        "warm_freed": sum(r.get("min_instances") or 0 for r in delete),
        "warm_total": sum(r.get("min_instances") or 0 for r in rows),
    }


# Agent Engine enforces a per-minute write quota per region, and a delete is a
# write. The first real teardown deleted 11 of 42 and then took 31 consecutive
# 429s, so deletes are paced and quota errors retried with a backoff.
DELETE_PAUSE_SECONDS = 8.0
DELETE_MAX_ATTEMPTS = 4


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resourceexhausted" in type(exc).__name__.lower()


def execute_prune(
    plan: dict,
    delete_fn,
    confirm: bool = False,
    pause: float = DELETE_PAUSE_SECONDS,
    sleep_fn=None,
    progress_fn=None,
) -> dict:
    """Delete the planned engines. **Does nothing unless ``confirm``.**

    A failure does not abort the batch: partial progress should be legible
    rather than lost to whichever engine happened to fail first. Quota errors
    are retried with a backoff; anything else is not, since retrying a
    permission error only burns the quota budget that the next engine needs.
    """
    if not confirm:
        return {"deleted": [], "failed": {}, "dry_run": True}

    import time as _time

    nap = sleep_fn or _time.sleep
    deleted, failed = [], {}
    for i, row in enumerate(plan["delete"]):
        if i and pause:
            nap(pause)
        eid = row["id"]
        last = None
        for attempt in range(DELETE_MAX_ATTEMPTS):
            try:
                delete_fn(eid)
                deleted.append(eid)
                last = None
                break
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                if not _is_quota_error(exc) or attempt == DELETE_MAX_ATTEMPTS - 1:
                    break
                nap(pause * (attempt + 2))
        if last is not None:
            failed[eid] = last
        if progress_fn:
            progress_fn(len(deleted), len(failed), len(plan["delete"]))
    return {"deleted": deleted, "failed": failed, "dry_run": False}


def referenced_ids(paths: tuple[str, ...] | list[str] = DEFAULT_REFERENCE_PATHS) -> set[str]:
    """Engine ids named as the value of an engine-id key under ``paths``.

    Keyed matches only. Matching bare 15-20 digit numbers returned 728 hits,
    almost all of them floats and timestamps inside experiment result files.
    """
    found: set[str] = set()
    for raw in paths:
        p = Path(raw)
        files = (
            [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else ([p] if p.is_file() else [])
        )
        for f in files:
            try:
                found.update(_ENGINE_ID_REF.findall(f.read_text(errors="ignore")))
            except OSError:
                continue
    return found


_TRAFFIC_FILTER = (
    'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
    'AND textPayload:"POST /api/stream_reasoning_engine"'
)


def engine_traffic(days: int = DEFAULT_WINDOW_DAYS, project: str | None = None) -> dict[str, int]:
    """Requests served per engine over the window, from one Cloud Logging query.

    One query counted client-side, not one per engine: at 80 engines that is the
    difference between a few seconds and a few minutes.
    """
    result = subprocess.run(
        [
            "gcloud",
            "logging",
            "read",
            _TRAFFIC_FILTER,
            f"--project={project or GCP_PROJECT_ID}",
            f"--freshness={days}d",
            "--limit=50000",
            "--format=value(resource.labels.reasoning_engine_id)",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=900,
    )
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        eid = line.strip()
        if eid:
            counts[eid] = counts.get(eid, 0) + 1
    return counts


def list_engines() -> list[dict]:
    """Inventory every Agent Engine in the project, with what the policy needs."""
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    rows = []
    for a in agent_engines.list():
        g = a.gca_resource
        spec = getattr(g, "spec", None)
        dep = getattr(spec, "deployment_spec", None) if spec else None
        rows.append(
            {
                "id": a.resource_name.split("/")[-1],
                "resource_name": a.resource_name,
                "display_name": a.display_name or "",
                "labels": dict(getattr(g, "labels", {}) or {}),
                "min_instances": getattr(dep, "min_instances", None) if dep else None,
                "create_time": str(a.create_time)[:16],
            }
        )
    return sorted(rows, key=lambda r: r["create_time"])


def delete_engine(engine_id: str) -> None:
    """Delete one engine. ``force`` because a deployed engine has child resources."""
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    resource = (
        engine_id
        if engine_id.startswith("projects/")
        else f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"
    )
    agent_engines.get(resource).delete(force=True)


def _counts_line(plan: dict) -> str:
    total = len(plan["delete"]) + len(plan["keep"])
    return f"  delete {len(plan['delete'])}, keep {len(plan['keep'])} (of {total})"


def _warm_line(plan: dict) -> str:
    after = plan["warm_total"] - plan["warm_freed"]
    return f"  always-warm instances: {plan['warm_total']} -> {after} ({plan['warm_freed']} freed)"


def render_plan(plan: dict, window_days: int = DEFAULT_WINDOW_DAYS) -> list[str]:
    """Render the prune plan for a human to check before anything is deleted."""
    lines = [
        "=" * 78,
        f"ENGINE PRUNE PLAN — traffic window {window_days}d",
        "=" * 78,
        _counts_line(plan),
        _warm_line(plan),
        "",
        f"DELETE ({len(plan['delete'])})",
    ]
    for r in plan["delete"]:
        warm = f" warm={r['min_instances']}" if r.get("min_instances") else ""
        lines.append(f"  {r['create_time']}  {r['display_name'][:32]:32s} {r['id']:22s}{warm}")
    lines += ["", f"KEEP ({len(plan['keep'])})"]
    by_reason: dict[str, int] = {}
    for r in plan["keep"]:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {count:3d}  {reason}")
    return lines


def build_plan(window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Inventory, gather evidence, and plan. Reads only."""
    rows = list_engines()
    return plan_prune(rows, traffic=engine_traffic(window_days), referenced=referenced_ids())


def snapshot_path(out_dir: str = "docs/notes") -> Path:
    """Where the pre-teardown inventory goes, so a decision stays traceable."""
    return Path(out_dir) / f"engine-inventory-{datetime.now(tz=UTC):%Y-%m-%d}.md"


def _snapshot_intro(total: int, since) -> str:
    return f"Snapshot taken before a teardown. {total} engines; traffic measured since {since}."


def render_snapshot(plan: dict, window_days: int = DEFAULT_WINDOW_DAYS) -> str:
    """A dated record of every engine and its disposition.

    Written before deleting. A deletion that turns out wrong should trace to a
    decision rather than have to be reconstructed from memory.
    """
    since = (datetime.now(tz=UTC) - timedelta(days=window_days)).date()
    total = len(plan["delete"]) + len(plan["keep"])
    out = [
        f"# Agent Engine inventory — {datetime.now(tz=UTC):%Y-%m-%d}",
        "",
        f"Snapshot taken before a teardown. {total} engines; traffic measured since {since}.",
        "",
        f"- **delete:** {len(plan['delete'])}",
        f"- **keep:** {len(plan['keep'])}",
        f"- **always-warm instances:** {plan['warm_total']} -> "
        + str(plan["warm_total"] - plan["warm_freed"]),
        "",
        "The policy and its reasoning live in [engine-lifecycle.md](engine-lifecycle.md).",
        "",
        "| disposition | created | display name | id | warm | reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for tag, rows in (("DELETE", plan["delete"]), ("keep", plan["keep"])):
        for r in sorted(rows, key=lambda x: x["create_time"]):
            warm = r.get("min_instances") or ""
            out.append(
                f"| {tag} | {r['create_time']} | `{r['display_name']}` | `{r['id']}` "
                f"| {warm} | {r['reason']} |"
            )
    return "\n".join(out) + "\n"
