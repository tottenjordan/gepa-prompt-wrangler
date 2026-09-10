# Resubmission: what it costs us, and what it could buy

**Opened:** 2026-09-10. **Status:** investigation, not a decision. Nothing here has been
implemented.

**Scope.** What happens when the same manifest is submitted more than once — today it
silently destroys the previous run's results — and whether fixing that is worth the work.
The mechanics of the bug are [silent-failures.md](silent-failures.md) #14; this note is
about the *decision*.

> Filename note: asked for as `resubmission_investiagtion.md`. Renamed to match the
> directory's kebab-case convention and to fix the typo, so links do not have to carry it.

---

## What we have observed

Three things, all from 2026-09-09, all first-hand.

**1. A resubmission overwrites its predecessor's artifacts in place.**
`run_id` is a hash of manifest name + agent module + eval data + pair ids. The GCS
artifact prefix is keyed on the *same* value — `pipeline-runs/run-<run_id>/stages/...` —
so the second run of a manifest writes over the first. Campaign 07 was submitted four
times under `run-8a5905dee0`; only the last survives.

**2. It is silent in the strongest sense.** Every layer behaves correctly. The pipeline
succeeds, the writes succeed, and `gsutil ls` shows a complete, consistent result set with
fresh timestamps. There is no partial state and no error. The only signal is *remembering*
that an earlier run reported something else.

**3. It fired twice in one day, and the second time changed a published conclusion.**
`docs/analysis/2026-09-09-c07-first-calibrated-result.md` was written from one run,
merged, and then had its `optimize` and `eval_after` artifacts overwritten at 17:11 and
17:52 UTC by the next run of the same manifest. Caught only because the numbers were
pulled again for an unrelated reason. Corrected in PR #58.

### The accident that turned this into a methodology finding

`eval_before` was a KFP cache hit and was **not** rewritten. So the two runs shared a
baseline exactly — same manifest, seed, model, criteria, budget, `num_runs` — and differed
only in GEPA's stochastic search. That is the first same-manifest repeat this project has
ever had, and it was produced by the bug rather than by design:

| metric | run A | run B | spread / control-arm floor |
| --- | --- | --- | --- |
| `hallucination_v1` | -0.0778 | +0.0172 | **12.3x** |
| `tool_use_quality_v1` | -0.0545 | +0.0205 | 4.6x |
| `final_response_quality_v1` | -0.0309 | -0.0059 | 2.3x |
| `instruction_following_v1` | -0.0448 | -0.0615 | 1.1x |
| `safety_v1` | +0.1536 | +0.1568 | 0.1x |

Optimized prompts: 6,067 vs 3,873 characters from the same 78-character seed.

**The consequence, now in CLAUDE.md:** a control arm holds the prompt fixed, so it bounds
*evaluation* noise and says nothing about the optimizer's variance — which was the larger
term on three of five metrics. An optimizing arm needs a **repeat**, and `num_runs` does
not provide one (it averages the evaluation of a single optimized prompt, not the choice
of prompt).

**So the bug and the fix are the same object.** Repeats are the thing we now know we need,
and overwriting is precisely what makes them impossible to keep.

---

## What we still need to test or confirm

Ordered by how much each would change the decision. 1 and 2 were closed on 2026-09-10;
3-5 are open.

1. ~~Does anything besides the stage artifacts collide?~~ **Confirmed 2026-09-10, and it
   hands us the fix.** `run-5aa73d6191` took three submissions. Under it:

   ```
   run-5aa73d6191/
     code.tar.gz            <- one copy, overwritten
     manifest.json          <- one copy, overwritten
     stages/                <- one copy, overwritten      (the data loss)
     reports/               <- one copy, overwritten      (the published analysis)
     pipeline_root/934903580331/
       gepa-run-5aa73d6191-20260909-023500/   <- all three
       gepa-run-5aa73d6191-20260909-081905/      submissions
       gepa-run-5aa73d6191-20260909-131445/      coexist
   ```

   **KFP already solved this one level down**, keying on the *job* name — which is the run
   id plus a submission timestamp. We do not need to invent a scheme; we need to use the
   one already in the same bucket. That also means the cache and the artifacts are
   *already* keyed differently by KFP itself, which is direct evidence the split is safe.
2. ~~Is `reports/` overwritten too?~~ **Confirmed. Yes.** One `reports/` for three
   submissions, and `experiment_report.md` carries the last one's timestamp (23:33 UTC).
   So a resubmission silently replaces the published analysis as well as the raw numbers.
3. **What exactly does KFP cache on?** CLAUDE.md says component body hash + input
   parameter values. If `run_id` is load-bearing as an *input value*, the cache key must
   keep it — which the fix allows, since only the artifact path changes. Still worth
   confirming that no component derives its cache identity from the artifact prefix.
   **Unverified, and now the only real unknown.**
4. **Does a submission-scoped prefix break `wrangler floor` and the analysis path?** They
   take bare run ids today. A "latest submission wins, older ones still readable"
   resolution rule needs to exist before the write side changes.
5. **How often do we actually resubmit?** Four times for campaign 07 during recovery.
   If that is typical, the exposure is large; if it was pathological, less so.

---

## What it is worth

### The case for doing it

- **It unblocks the methodology fix.** Repeats are impossible to retain while the second
  run erases the first. Campaign 08 cannot be designed around repeats until this is fixed.
  This is the strongest argument and it did not exist before 2026-09-09.
- **It is silent and total.** No error, no partial state, no way to notice after the fact.
  Every other failure in `silent-failures.md` at least leaves evidence.
- **It has already cost us a published conclusion once**, and we caught that by luck.
- **The current mitigation is manual and unenforced** — `gsutil -m cp -r` the prefix
  before each resubmission. It was done by hand for two runs on 2026-09-09 and nothing
  makes it happen again.

### The case against, or for waiting

- **Nothing is currently blocked.** Campaign 07 is finished. No run is at risk today.
- **The obvious fix is wrong** and the right one is more invasive. Making `run_id` unique
  would fix the artifacts and destroy the cache — campaign 07's recovery depended on a
  cache hit worth ~7 hours of optimize. The correct shape is to *split* the key: cache key
  stays deterministic, artifact prefix gains a submission-scoped component.
- **It touches `components.py`**, which invalidates the KFP cache for that component. If
  another campaign-07 resubmission is planned, doing this first forfeits the cache hit.
- **Read-side compatibility is real work.** Existing analyses reference bare run ids;
  those links must keep resolving.

### Rough sizing

Small-to-medium and mostly mechanical: five call sites in `deploy_pipeline.py` and
`components.py` write under `pipeline-runs/{run_id}/`, and the job name already carries a
timestamp (`gepa-run-5aa73d6191-20260909-131445`) that is a ready-made submission scope.
The risk is concentrated in the read side and in the cache interaction, not in the writes.

**Recommendation:** do it before campaign 08 is designed, and not before `c07-pro`'s
results are read. It is a prerequisite for repeats, and repeats are the main open
methodological gap. It is not urgent in the sense of anything being at risk this week.

---

## Interim mitigation, in force now

Before resubmitting a manifest whose earlier run produced results anyone cares about:

```bash
gsutil -m cp -r gs://$GCP_STAGING_BUCKET/pipeline-runs/run-<id> \
                gs://$GCP_STAGING_BUCKET/pipeline-runs/archive/run-<id>-<date>
```

Already done for the two runs behind the campaign 07 analysis:

- `pipeline-runs/archive/run-8a5905dee0-2026-09-09/`
- `pipeline-runs/archive/run-70166a6bc8-2026-09-09/`

## Related

- [silent-failures.md](silent-failures.md) #14 — the mechanics and both occurrences.
- [silent-failures.md](silent-failures.md) #13 — merging to `main` under a live driver,
  the incident whose recovery caused the four resubmissions.
- [../analysis/2026-09-09-c07-first-calibrated-result.md](../analysis/2026-09-09-c07-first-calibrated-result.md)
  — the analysis this destroyed and the two-run comparison it accidentally produced.
