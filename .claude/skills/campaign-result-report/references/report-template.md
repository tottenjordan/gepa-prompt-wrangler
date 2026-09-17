# Campaign result report — template

Fill the placeholders. **Delete sections that do not apply rather than leaving them empty** —
an empty "Limits" section reads as "no limits", which is never true.

Match the tone of the existing files in `docs/analysis/`: state the finding, then
immediately state what would have to be true for it to be wrong.

---

```markdown
# <Campaign> — <the finding, in one line>

**Date:** YYYY-MM-DD
**Pre-registration:** [../doe/<n>-<slug>.md](../doe/<n>-<slug>.md)
**Pipeline job:** `<job id>`
**Engine(s):** `<id>` (<display name>), health gate <result>
**Eval set:** `<path>`, <N> cases
**Knobs:** num_runs=<r>, score_repeats=<s>
**Status:** <complete | unresolved | partial>

## Headline

<Two or three sentences. The finding, and the single number that carries it, with its floor.
If the campaign was UNRESOLVED, say that here rather than burying it.>

## Results

| metric | control (floor) | <arm A> Δ | <arm B> Δ | verdict |
| --- | --- | --- | --- | --- |
| `safety_v1` | 0.00XX | +0.XXX | +0.XXX | <cleared / within-noise> |
| `instruction_following_v1` | 0.0XXX | −0.0XX | −0.0XX | <...> |
| `final_response_quality_v1` | | | | |
| `hallucination_v1` | | | | |
| `tool_use_quality_v1` | | | | |

Coverage: <cases_scored/total per arm per side>. <Note any side that lost cases.>

## Against the pre-registration's decision table

| pre-registered outcome | what happened |
| --- | --- |
| <outcome 1> | <yes/no, and the number> |
| <outcome 2> | <...> |

<Include every row the pre-registration listed, including the ones that did not happen.>

## What this cannot say

<Required section. At minimum:>

- **n=<N> per condition.** <What that does and does not support.>
- **The floor is from this run's control arm** (<arm id>), drift <value>. <Whether it gated.>
- **<Any metric whose MDE exceeds the observed effect>** — MDE <value> against an observed
  <value>, so a null here is not evidence of no effect.
- <Confounds: what else moved at the same time.>

## Cost

| | estimated | actual |
| --- | --- | --- |
| <stage> | | |
| whole campaign | | |

## What to do with this

1. <Concrete next action, not "investigate further".>
2. <Any doc that now has to change — CLAUDE.md numbers, silent-failures entries, a DOE status.>
```

---

## Notes on filling it in

**The headline is the finding, not the activity.** "Per-case judge disagreement does not
predict the aggregate floor" beats "DOE 03 results". A reader scanning `docs/analysis/`
should learn the conclusion from the filename and first line.

**Put the floor in the same table as the delta.** Not in a footnote. The whole failure mode
this template guards against is a number travelling without its bar.

**"What this cannot say" is not boilerplate.** If you cannot fill it with three specific
limits, you have not understood the run well enough to publish it.

**Cost table earns its place.** Estimates in this repo have been wrong by 40% and knowing
that is what makes the next plan honest.

**Cross-link both ways.** Add the report to `docs/notes/README.md` if it carries a lesson,
and update the pre-registration's Result section to point at it.
