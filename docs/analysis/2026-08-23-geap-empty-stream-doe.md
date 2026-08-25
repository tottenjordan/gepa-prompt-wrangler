# GEAP returns 200 with no inference — measured, and the cause was wrong

**Run:** 2026-08-23 · two replicates × four engines × 120 attempts = **960 attempts**
**Artifacts:** `outputs/probes/doe_rep{1,2}.jsonl` and `.joined.jsonl`
**Tools:** `wrangler/tools/boot_probe.py`, `wrangler/tools/boot_probe_join.py`

This exists because the evidence for defect #5 was about to be sent to Google and did not
survive being re-measured. Three of the four headline numbers in
[../notes/silent-failures.md](../notes/silent-failures.md) #5 moved when checked against
live logs, and the mechanism the note names turns out not to be what happens.

The defect is real and worth escalating. The explanation attached to it was not.

---

## Design

Four engines, deployed back to back, identical except for two factors:

| arm | toolsets | model |
| --- | --- | --- |
| `mcp-claude` | 3 MCP | `DEFAULT_AGENT_MODEL_ALT` |
| `bare-claude` | none | `DEFAULT_AGENT_MODEL_ALT` |
| `mcp-gemini` | 3 MCP | `DEFAULT_AGENT_MODEL` |
| `bare-gemini` | none | `DEFAULT_AGENT_MODEL` |

Same trivial instruction ("reply with exactly the word OK"), same `min_instances: 2` passed
explicitly, same region. The toolsets factor exists to answer *"is it your agent's slow
startup?"* — the obvious first objection, which no previous measurement could address
because every one was taken against the three-MCP-handshake agent. The model factor
pre-empts *"is it Claude, or the global endpoint?"*.

**Pre-registered before any data was seen:** 120 attempts per arm per replicate, one request
in flight per engine, 5s spacing, four labelled blocks of 30, arms concurrent across
engines. Two replicates at different times. Report whatever 2×120 gives; do not extend a
promising arm. That rule was written because the last time an arm looked like a 5× win at
n=12 it collapsed to nothing at n=30.

**No retries.** A retry collapses independent draws into "did any of six work", which is the
accounting that hid this defect's size for as long as it did.

### How each attempt is joined to what the server did

Two independent joins, because they fail independently:

- **the nonce join** — GEAP emits a structured log stream whose labels carry the full prompt
  in `gen_ai.input.messages`. Every probe prompt contains a unique nonce, so "did the model
  run for *this* request" is a lookup, not an inference. **Across 960 attempts this agreed
  with the client-side event count 960 times and disagreed zero times.**
- **the PID join** — places the request on a worker incarnation and gives that worker's age.
  948 of 960 (98.8%) joined; the 12 that did not are reported, not dropped.

A first version of the nonce join keyed on `labels."user.id"` and reported 0/4 served for an
arm whose four answers had just been watched arriving. That label is emitted by some engines
and not others. The prompt text is emitted consistently.

---

## Result 1 — the defect is real and large

**656 of 960 attempts reached the model. 31.7% did not** — HTTP 200, zero events, no error,
and no inference performed anywhere on the server.

| arm | replicate 1 | replicate 2 | pooled |
| --- | --- | --- | --- |
| `mcp-claude` | 18.3% [0.12–0.26] | 46.7% [0.38–0.56] | **32.5%** [0.269–0.387] |
| `bare-claude` | 87.5% [0.80–0.92] | 91.7% [0.85–0.95] | **89.6%** [0.851–0.928] |
| `mcp-gemini` | 95.0% [0.90–0.98] | 96.7% [0.92–0.99] | **95.8%** [0.925–0.977] |
| `bare-gemini` | 56.7% [0.48–0.65] | 54.2% [0.45–0.63] | **55.4%** [0.491–0.616] |

Reach rate, Wilson 95% CI, n=120 per cell per replicate.

## Result 2 — the cold-worker mechanism is refuted

The note says GEAP "routes a request to a worker that has not finished starting up" and that
"the request is consumed during the ~8s boot". Of 948 joined attempts:

| | n | reach |
| --- | --- | --- |
| served by a worker that had **finished booting** | **948** | 69.2% [0.662–0.721] |
| served by a worker **still booting** | **0** | — |

**Every empty 200 — all 292 of them — came from a worker that had already completed
startup.** Median serving-worker age at request: **215 seconds**. Even the 10th percentile is
39s. Nothing is being eaten during a boot.

Reach against worker age is flat, not a dose-response:

| worker age at request | n | reach |
| --- | --- | --- |
| 0–2s | 6 | 83.3% |
| 2–10s | 13 | 53.8% |
| 10–30s | 54 | 75.9% |
| 30–90s | 132 | 66.7% |
| 90s+ | 743 | 69.3% |

If anything the youngest workers do *better*, though n=6 there carries no weight.

Three supporting facts point the same way:

- **Application startup is sub-second on every arm** — median 0.00s from `Started server
  process` to `Application startup complete`. The three MCP handshakes run on a *background
  thread after* startup completes, so they never blocked anything. The note's "~8s startup"
  is not supported. (An earlier figure of "median 3.5s, p90 27s" measured *first log line →
  startup complete*, which is a different quantity and was itself never the boot the note
  described.)
- **Latency does not distinguish the two outcomes.** Reached: median 10.5s, p90 20.7s.
  Empty 200: median 10.8s, p90 22.7s. The note's "empty responses take 5–15s, i.e. they wait
  for the boot and then return nothing" describes a difference that is not there.
- **GEAP can return an error, and sometimes does.** 12 of 960 attempts (1.2%) returned
  `400 … Service Unavailable` — visible, attributable, retryable. The other 292 failures
  chose 200 instead. The capability exists; this path does not use it.

## Result 3 — what the factors did, and why the design cannot finish the job

There is no consistent main effect. Within Claude, removing MCP took reach from 32.5% to
89.6%. Within Gemini it went the other way, 95.8% down to 55.4%.

**One engine per cell means engine identity and cell are perfectly confounded.** A per-engine
idiosyncrasy is indistinguishable from a factor effect, and the spread between engines
(32.5% to 95.8%) is far larger than anything the factors plausibly explain. This is a
limitation of the design as run, and it is the main thing a follow-up should fix: two or
three engines per cell, not one.

What the arms *do* establish, and it is the point that matters for the escalation:

- **The defect is not caused by MCP toolsets.** `bare-gemini` — no tools at all, one
  in-process memory tool, sub-second startup — failed 44.6% of requests. Removing every
  toolset does not remove the defect.
- **The defect is not Claude-specific or global-endpoint-specific.** Both model families
  produce it, and the best and worst arms are one of each.
- **Per-engine rates are stable in time but differ enormously between engines.** Block-level
  rates within a 70-minute run varied little (e.g. `mcp-gemini` 93/100/97/90%). Across a few
  hours, three of four arms replicated within a couple of points — while `mcp-claude` moved
  18.3% → 46.7%, so an engine can also shift substantially between sessions.

One suggestive correlation, offered as a lead rather than a finding: `mcp-claude`, the worst
arm, was also the only one spawning more workers than it served requests (1.68 boots per
request, against 0.75–0.95 for the others).

## What this changes

**In the note.** The symptom stands and the blast radius stands. These do not:

| claim | status |
| --- | --- |
| "routes to a worker that has not finished booting" | **refuted** — 0 of 948 |
| "startup is ~8s: three MCP handshakes" | **refuted** — sub-second, and non-blocking |
| "empty responses take 5–15s, i.e. they wait for the boot" | **refuted** — same latency as successes |
| "~24% of requests reach the model" | **superseded** — 68.3% [0.653–0.712] here |
| "1.3 startups per stream request" | **superseded** — 0.75–1.68, engine-dependent |
| PID arithmetic is a floor because PIDs recur | **partly** — reuse is real and varies, but incarnations handle it |

**In what we ask Google for.** "Stop admitting requests to booting workers" was going to be
the ask. It would have been the wrong fix for the wrong cause. The ask is now narrower and
better supported: a request GEAP cannot serve must not return 200 with an empty body, because
that is indistinguishable from a valid empty answer and no client can retry it correctly —
and the 1.2% that return `400 Service Unavailable` show the mechanism already exists.

## Reproducing

```bash
uv run python scripts/deploy_probe_arms.py
uv run python -m wrangler.tools.boot_probe --arm bare-gemini=<id> --n 120 --spacing 5
uv run python -m wrangler.tools.boot_probe_join outputs/probes/<run>.jsonl --lead-in 90
```

`--lead-in` matters: at the default 15 minutes an idle engine's workers predate the window
and every age comes back a lower bound. The join says so rather than quietly reporting them.

## Related

- [../notes/silent-failures.md](../notes/silent-failures.md) #5 — the defect, now corrected
- [2026-08-22-first-optimization-sweep.md](2026-08-22-first-optimization-sweep.md) — the
  unbalanced case counts this defect produces
