# Agent Engine lifecycle — how they accumulate, and how they are reaped

**Verified 2026-08-24**, when the project held **80 engines**, the oldest from 2026-02-13.
Tool: `wrangler engines list` / `prune` (`wrangler/tools/engines.py`).

## Why they accumulate

CLAUDE.md forbids pinning engine ids — a good rule, because an id names one deployment and
whether a change means update, redeploy or a new engine is decided at the time. The
consequence is that **nothing in the repo names them, so nothing reaps them either**. One
campaign added fourteen in two days without anyone noticing the total.

## The count is not the cost

Of 80 engines, **31 held `min_instances` and accounted for 61 always-warm instances**. The
other 49 scaled to zero and cost nothing idle. So the 23 stale `gepa-*` engines from June
were clutter, not spend, and **all 28 reclaimable warm instances sat on probe engines from
the previous two days**. Reach for `min_instances`, not for `create_time`, when deciding
what is urgent.

## The policy: every signal has a veto

An engine is deletable only when **all** of these agree. Any single one protects it.

| Signal | Protects when |
| --- | --- |
| **Ownership** | not labelled `solution=promp-wrangler` (or on the named legacy list) |
| **Traffic** | any `POST /api/stream_reasoning_engine` in the window — *waived for ephemeral* |
| **Reference** | its id appears in `.env`, a manifest, or an experiment config |
| **Warmth** | `min_instances > 0` **and** referenced — someone is holding it hot on purpose |

### Why the ownership veto is the important one

**Only 48 of the 80 were labelled ours.** Three of the *unlabelled* engines were the busiest
in the project — `coordinator_agent_jt1` (8,576 requests in 30 days), `coordinator_agent`
(3,101), `router_agent_jt1` (2,397) — and eight more were `solution=geap-tour`. **A sweep by
age or by name prefix would have deleted live work belonging to someone else.**

So: no label saying it is ours means it is not ours to delete. Nineteen idle unlabelled
engines survive this policy indefinitely, and that is the correct trade — they scale to zero,
and clutter is cheaper than destroying someone's work.

### Two named exceptions, not two patterns

Both are module constants with a written reason per entry, so extending either is a visible,
argued diff rather than a regex that quietly widens.

- **`LEGACY_UNLABELLED`** — five engines from 2026-05-29 that predate the labelling
  convention and carry this repo's exact naming from that era. Deliberately excludes
  `sonnet_agent` (2026-05-21), whose underscore style matches the `geap-tour` set. Looking
  similar is not evidence.
- **`EPHEMERAL_PRE_LABEL`** — fourteen probe engines from campaigns that predate the
  `lifecycle` label.

### Ephemeral engines waive the traffic veto

The first run of this policy kept **all fourteen probe engines**, protected by "traffic in
window" — 100 to 244 requests each, every one of them sent *by the probe measuring them*.
Self-generated load is not evidence that anything depends on an engine.

So an engine labelled `lifecycle: ephemeral` waives the traffic veto. Every other veto still
applies: an ephemeral engine that is referenced, or that is not ours, stays.

`deploy_agent_from_source(labels=...)` merges over `{"solution": "promp-wrangler"}` rather
than replacing it — an engine that loses the ownership label becomes unreapable by this
policy, so a caller cannot drop it by accident.

## Deleting is rate-limited

Agent Engine enforces a **per-minute write quota per region**, and a delete is a write. The
first teardown deleted 11 of 42 and then took **31 consecutive 429s**. `execute_prune` now
paces deletes ~8s apart and retries quota errors with a backoff; non-quota errors are not
retried, since that only burns the budget the next engine needs.

A failure never aborts the batch. Partial progress should be legible rather than lost to
whichever engine happened to fail first — that design is what made the first attempt
recoverable rather than a mystery.

## The 2026-08-24 teardown

| | before | after |
| --- | --- | --- |
| engines | 80 | **38** |
| always-warm instances | 61 | **33** |

42 deleted: 14 probe engines, 23 idle `gepa-*` labelled ours, 5 legacy unlabelled. Every
protected engine survived and every referenced id still resolves. Per-engine dispositions
are in [engine-inventory-2026-08-24.md](engine-inventory-2026-08-24.md).

## Rules to keep it from recurring

1. **A campaign is not complete until its engines are gone.** They are the last step of the
   write-up, not a separate chore. See [../doe/README.md](../doe/README.md).
2. **Ephemeral resources declare themselves** with `lifecycle: ephemeral` and `campaign: <id>`
   at deploy time, so a sweeper finds them by evidence rather than by remembering a naming
   prefix.
3. **Run `wrangler engines list` before deploying a batch.** Cheap, and it is how a pile-up
   becomes visible before it is eighty.

## Not covered here

Cloud Run MCP services, Artifact Registry images and GCS staging artifacts accumulate the
same way and have not been audited. Worth its own pass.
