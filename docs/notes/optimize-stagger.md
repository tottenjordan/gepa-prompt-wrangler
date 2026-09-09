# The 90-minute optimize stagger does less than it says

**Measured 2026-09-09, on campaign 07 batch 1.** Two findings. Neither is
urgent; both need the campaign to finish first.

`scripts/run_campaign.py:OPTIMIZE_STAGGER` waits 90 minutes between the
optimizing arms of a batch, because they share the `gemini-3.5-flash` judge.

## The contention is real — start there

`gemini-3.5-flash` is **RPM 5** in the registry. Campaign 07's validation arm
logged **76 × HTTP 429** and ~300 rate-limit lines during its optimize phase,
*with* the stagger in place. So the problem the stagger exists for is genuine,
and neither finding below is an argument for deleting it outright.

## 1. It waits even when the previous arm was a cache hit

`_needs_stagger()` reads the manifest — whether the pair declares
`skip_optimize`. It cannot know that a *submitted* arm will come back SKIPPED.

Campaign 07 batch 1 re-submits the validation arm on purpose, expecting KFP to
cache it (`validate_then_run.py:123`). It did:

```
batch-1 c07-sonnet5: SUCCEEDED in 16m
    deploy-single-agent      SKIPPED
    eval-single-agent        SKIPPED
    optimize-single-agent    SKIPPED
```

A skipped optimize consumes **no judge quota**. The runner then waited 90
minutes before `c07-pro` to space it from an arm that spent nothing and had
already finished.

**Fix:** after submitting, check the job's task states and skip the stagger if
`optimize-single-agent` came back SKIPPED. The information is available — it is
just not consulted.

## 2. Ninety minutes is a small fraction of an optimize phase

The validation arm's optimize ran **577 minutes**. Offsetting two arms by 90
minutes leaves them overlapping for roughly **8 of those 9.6 hours**, so the
stagger de-conflicts about the first 16% of the run and then the arms contend
exactly as they would have.

The comment says offsetting the starts "keeps them out of step". That is true
of the starts and not of the run, and the 76 × 429 is what that distinction
costs.

**Fix, pick one:**

- Size the stagger against a measured optimize duration rather than a round
  number — though at 577 min a *useful* stagger is longer than most people will
  accept, which is itself an argument for the next option.
- Handle 429s properly (backoff and retry on the judge call) and drop the
  stagger to something small. The rate limiting is happening anyway; the
  stagger only changes how much of it lands at once.

## Why nothing was changed at the time

Campaign 07 was mid-flight with five arms still to release. Editing
`run_campaign.py` while a driver is watching risks the release for a
90-minute saving. Batch 2's stagger is *correct* — both its optimizing arms are
genuine, uncached runs — so the defect costs one occurrence per campaign, not
one per batch.
