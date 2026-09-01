# Probe datasets

Raw evidence behind the engine-health findings. Committed gzipped because
`outputs/` is gitignored and the `.joined` variants **cannot be regenerated**:
they are reconstructed from Cloud Logging, whose default retention is 30 days.
`doe_rep1` was collected 2026-08-23, so its logs age out around 2026-09-22.

Read one with:

```bash
gzip -dc docs/data/probes/lottery_a.joined.jsonl.gz | head -1 | python3 -m json.tool
```

## What is in a row

Every probe attempt is one JSON object: `arm`, `engine_id`, `nonce`, `prompt`,
`sent_at`, `finished_at`, `latency_s`, `event_count`, `reached`, `error`.

`reached = event_count > 0` — a **single-attempt** reach rate. This is not the
same quantity as an eval's coverage, which is a completion rate after
`EVAL_MAX_RETRIES` attempts. The two differ by roughly 50x on a degraded engine
and confusing them cost a day; see
[../../analysis/2026-09-01-health-gate-vs-eval-coverage.md](../../analysis/2026-09-01-health-gate-vs-eval-coverage.md).

A `.joined` file adds the fields that make the per-worker analysis possible, by
matching each request's nonce against the engine's structured log stream:
`serving_pid`, `worker_age_s`, `worker_age_is_lower_bound`,
`booted_before_request`, `reached_model`, `model_join`, `joinable`, `join_note`.

`reached_model` is the trustworthy one. `reached` is a client-side observation;
`reached_model` says the model actually ran for *that* request. Prefer it.

## The datasets

| File | n | Collected | Backs |
| --- | --- | --- | --- |
| `doe_rep1{,.joined}` | 480 | 2026-08-23 | [escalation](../../escalations/2026-08-23-geap-empty-stream.md) — replicate 1 of the 4-arm DOE (bare/mcp × claude/gemini) that refuted the MCP-tools and cold-worker hypotheses |
| `doe_rep2{,.joined}` | 480 | 2026-08-23 | Same, replicate 2. Reported separately before pooling, per the pre-registration |
| `lottery_a{,.joined}` | 1000 | 2026-08-24 | [Campaign 01](../../doe/01-engine-lottery.md) Phase A — ten byte-identical engines, 100 attempts each. The 0%–100% spread |
| `lottery_b{,.joined}` | 400 | 2026-08-24 | Campaign 01 Phase B — the reroll. Joined 2026-09-01, eight days after collection and three weeks before the logs would have expired |
| `time_control` | 120 | 2026-09-01 | [Opus serving failure](../../analysis/2026-09-01-opus-serving-failure.md) — the concurrent 4-tier control. flash 100%, lite 100%, pro 97%, sonnet 97% |
| `v4_health` | 150 | 2026-08-31 | [MCP flakiness](../../analysis/2026-08-31-mcp-flakiness.md) — the v4 baseline |
| `v5_health` | 90 | 2026-08-31 | Opus analysis — opus 27%, and **lite 0/30** |
| `warm-recheck_3126647680801964032` | 60 | 2026-09-01 | Health-gate analysis — the engine the gate rejected at 1.7%, re-probed warm at 25% |

`warm-recheck-*.summary.json` is the four-line gate decision for that last run,
kept uncompressed so it is greppable.

## What was discarded, and why

Deleted rather than archived on 2026-09-01. Recorded here so nobody looks for them:

- `smoke_all_arms{,.joined}` (n=16) — the smoke test that preceded the real DOE.
  Strictly superseded by `doe_rep1`/`doe_rep2`.
- `gate_bare_claude{,.joined}` (n=3) — a connectivity check, not a measurement.
- `gate_12377752149688320` (n=20) — a 20-attempt probe of an engine that
  `lottery_b` covers with 100 attempts. A subset of a dataset that is kept.
- `gate_6346690628046290944` (n=32) — an ad-hoc probe of an engine that no
  longer exists and is not named by any campaign.
- `warm-recheck-*.txt` — duplicate of the `.summary.json`.

## Adding to this directory

Keep a dataset if a committed document cites a number derived from it. If it
does not, say why it is here anyway — an unlabelled JSONL is worthless in six
months, which is the entire reason this file exists. Run the join **before**
the 30-day log retention expires, or the per-worker fields are gone for good.
