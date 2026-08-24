# DOE Campaigns — Index

Pre-registered experiments. One file per campaign, all with the same headings:
**Question · Design · Pre-registration · What each outcome would mean · Cost · Result**.

The Result section stays empty until the campaign runs. That ordering is the whole point:
in this repo an arm once looked like a **5× win at n=12 and collapsed to nothing at n=30**
(silent-failures #5), and a three-arm sweep was nearly reported as a clean win against a
noise floor that turned out to explain it. Writing down n, the stopping rule, and what each
outcome would mean *before* looking is what stops that.

## Campaigns

| # | Campaign | Status | Cost |
|---|----------|--------|------|
| [01](01-engine-lottery.md) | Is the engine failure rate a deployment lottery? | **Complete 2026-08-24 — yes, and it is a per-worker property** | 1,400 attempts, ~6 h |
| [02](02-judge-variance.md) | How much of the noise floor is the judge? | **Not started** | One capture, then scoring only |
| [03](03-noise-floor.md) | What is the noise floor, really? | **Not started** | Moderate |
| [04](04-gepa-budget-and-criteria.md) | What does a GEPA budget buy? | **Not started** | ~90 h wall-clock |

Run order is dependency order. 01 stands alone and is the cheapest. 02 needs
`wrangler capture` / `wrangler score`. 03 needs 02's variance split and the multi-run
averaging fix. 04 needs 03's floor, or its results cannot be read.

## Rules that apply to every campaign here

- **Fix n and the stopping rule before collecting.** No extending a promising arm.
- **Every optimization sweep carries an unchanged-prompt control arm**, run first, as a
  gate, at the same `num_runs` as the real arms (CLAUDE.md).
- **Run arms sequentially, never concurrently**, where they share an engine or a rate
  limit. The 2026-08-22 sweep broke its own plan on this.
- **Report a null as readily as a hit.** Several of these are designed so that "no effect"
  is the more useful answer.
- **State what was dropped.** A silent truncation reads as full coverage.

## Related

- [../notes/silent-failures.md](../notes/silent-failures.md) — the defects these work around
- [../analysis/](../analysis/) — results from campaigns and one-off investigations
- [../escalations/](../escalations/) — what gets sent to a service owner
