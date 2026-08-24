# Campaign 02 — How much of the noise floor is the judge?

**Status:** Not started · **Depends on:** `wrangler capture` / `wrangler score`

## Question

The noise floor — the delta an unchanged prompt produces between two evaluations — is
attributed in [../notes/silent-failures.md](../notes/silent-failures.md) and CLAUDE.md to
"judge and agent non-determinism". Those have never been separated. Pairing on case index
was measured to remove only ~15% of the floor, and the remaining 85% is currently one
undifferentiated lump.

It matters which. If the **judge** dominates, the fix is cheap and needs no agent calls at
all — score more times, or change the ensemble. If the **agent** dominates, `num_runs` is
the only lever and every reduction costs a full inference pass against an engine that drops
a third of its requests.

## Design

`wrangler capture` makes this cheap and, more importantly, makes it *valid*: comparing two
judges is only meaningful against identical responses, and re-running inference guarantees
they are not identical.

| Arm | Method | Variance measured |
| --- | --- | --- |
| **Judge alone** | one capture, `wrangler score <capture> --repeat 5` | judge only — responses are byte-identical |
| **Agent + judge** | five captures from the same engine and cases, each scored once | agent + judge; subtract the above for agent alone |
| **Judge model** | one capture scored with each model in `DEFAULT_JUDGE_ENSEMBLE` | between-judge disagreement |
| **Tool-use prompt** | one capture scored with the current `_TOOL_USE_JUDGE_PROMPT` and with a JSON-hardened variant | the effect of the prompt change, per case |

The fourth arm settles a standing blocker. The `tool_use_quality` judge sometimes returns
unparseable JSON, and because of the `extra='forbid'` cascade one malformed response costs
that case *every* metric — the last in-repo cause of case loss (silent-failures #9). The
objection to fixing it has been that *"changing that prompt changes every score it
produces"*. Against identical responses, that stops being an objection and becomes a
measurement: exactly how much, and on which cases.

## Pre-registration

- **5 repeats × 64 cases** per condition. One capture per arm except "agent + judge", which
  needs five.
- Report **per-metric standard deviation and per-case disagreement rate**, not only the
  mean. A mean over five scorings hides the quantity being measured.
- Report the malformed-JSON rate for the tool-use arm directly, as a count.
- No arm is extended because it looks interesting.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Judge SD ≥ agent SD** | Recommend scoring repeats over `num_runs`; revisit `DEFAULT_JUDGE_ENSEMBLE`. A much cheaper floor reduction than more inference. |
| **Agent SD dominant** | `num_runs` is the only lever; Campaign 03 sets it and the cost is unavoidable. |
| **Both small** | The floor is coming from somewhere else — case sampling, or scoring-stage dropout — and Campaign 03 has to look there. |
| **Judge models disagree per case but agree on the mean** | An ensemble helps individual cases without moving aggregates. Worth knowing before trusting per-case analysis. |
| **Hardened tool-use prompt changes scores materially** | Ship it only as a deliberate, dated re-baseline, and say every prior tool-use number is not comparable across it. |
| **Hardened prompt fixes the JSON without moving scores** | Ship it. Removes the last in-repo case-loss cause for free. |

## Cost

One expensive capture per arm; scoring is cheap and, after the captures, involves **no agent
calls at all** — so the empty-stream defect cannot touch this campaign. That is a large part
of why it is worth building capture/score before running it.

## Result

_Not yet run._
