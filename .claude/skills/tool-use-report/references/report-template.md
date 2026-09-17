# Tool use report — template

**The contamination check comes first and is not optional.** A tool-use report that opens
with scores has already made the mistake this template exists to prevent.

---

```markdown
# <Run label> — tool use

**Date:** YYYY-MM-DD
**Pipeline job:** `<job id>`  ·  **Optimize job id(s):** `<numeric job id>`
**Agent / toolsets:** <agent module> — search, booking, expense
**ADK version:** <x.y.z>  ·  **Judge prompt:** <original | hardened (≥ 2026-09-17)>

## 1. Is this run trustworthy?

| check | value | reading |
| --- | --- | --- |
| toolset losses | <n> / <generations> = **<pct>%** | <clean / contaminated> |
| `Closing toolset` events | <n> | <non-zero required, else the query is broken> |
| `Deferred N toolset close(s)` | <n> | <non-zero means patch 8 engaged> |
| `--freshness` used | <e.g. 90d> | <must be stated> |

<One sentence: can the scores below be read as being about the prompt, or not.>

## 2. Tools: declared, available, called

| toolset | declared | available | called (n invocations) | notes |
| --- | --- | --- | --- | --- |
| search | yes | <yes/partial> | <n> | |
| booking | yes | <yes/partial> | <n> | |
| expense | yes | <yes/partial> | <n> | |

<Any toolset declared but never called is a finding — either the eval set does not exercise
it, or the agent does not know it exists.>

## 3. Scores

| arm | `tool_use_quality_v1` before | after | Δ | floor | verdict |
| --- | --- | --- | --- | --- | --- |
| <arm> | | | | | |

Coverage: <cases_scored/total per side>.

<If any score sits in 0.33-0.42, address the predefined-metric artifact explicitly.>

## 4. What this cannot say

- <Contamination rate, if non-zero, and what it does to the numbers above.>
- <Whether any comparison crosses the 2026-09-17 judge re-baseline.>
- <n, and what it supports.>

## 5. What to do with this

1. <Concrete action.>
```

---

## Notes on filling it in

**Section 1 gates sections 2–4.** If contamination is non-zero, say plainly that the scores
are uninterpretable and stop — do not publish a Δ with a caveat attached and hope the reader
carries it. Campaigns 07 and 08 are the worked example: their tool-use results are not
quoted anywhere as findings, and that is the correct treatment.

**Distinguish declared / available / called.** Most wrong conclusions about tool use come
from collapsing those three into "the agent didn't use the tool".

**Name the ADK version and the judge prompt.** Both changed the numbers in the past year,
and a report without them cannot be compared to its predecessors.

**Do not include a tool-category colour table.** Three MCP toolsets do not need one; that is
inherited scaffolding from a larger tool inventory and it would rot.
