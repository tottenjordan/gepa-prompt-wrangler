# Five ways to improve the prompt-optimization harness

**Method.** An exhaustive read of `wrangler/` paired with a fan-out literature search: 22 primary
sources fetched, 25 falsifiable claims extracted, each verified by three adversarial agents
(0 of 25 refuted). Every recommendation below is anchored to **a measured gap in this codebase**
and **a published method**, in that order. Where the literature contradicts something this repo
already believes — including something I shipped two days ago — that is called out rather than
smoothed over.

---

## Three corrections before the recommendations

These change how existing results should be read, so they come first.

**1. The bootstrap CIs I put on the campaign 09 reanalysis are in a class shown to be
miscalibrated at this sample size.** Bowyer et al. (ICML 2025 Spotlight, arXiv 2025-03-03)
measure *both* CLT and bootstrap intervals as poorly calibrated on LLM evals below a few hundred
datapoints — at N=100 a nominal-95% CLT interval achieved 92.5% coverage — and recommend Wilson
score intervals or Bayesian beta-binomial credible intervals instead, packaged as
`github.com/sambowyer/bayes_evals`. Our n is 63. The *direction* of the reanalysis stands
(paired per-case contrasts beat arm means); the interval widths should be redone.

**2. The scoring canary's comparison is the test that paper says false-alarms.** A judge-drift
paper (arXiv 2026-06-13) measures the industry-default rolling z-test on drift-free streams
**false-alarming 75% of the time** — an unadjusted repeated comparison of eval scores over time
is not a valid drift test. The canary I shipped does exactly an unadjusted repeated comparison.
Its readings are still useful as descriptive evidence; they are not a test. Fix in idea 5.

**3. GEPA is not the strongest option for a single-predictor program.** The MO-CAPO paper
(arXiv 2026-05-15) ranks GEPA **fourth of six** on a critical-difference diagram over 4 tasks ×
3 LLMs at a 7.5M-token budget, behind CAPO, MO-CAPO and NSGA-II-PO. Separately, the GEPA paper
itself (arXiv 2507.19457, ICLR 2026 Oral) attributes its single largest design win to
**Pareto-based candidate selection** (+7.33% over BeamSearch, +6.4% over SelectBestCandidate) —
not to reflection — and reports that **most of its rollout budget goes to validation scoring for
candidate selection, not to producing learning signal** (train-only rollouts to optimum: 79–737).
We spend a flat `max_metric_calls: 600`.

---

## Idea 1 — Give the harness a real held-out test set

> **OUTCOME (2026-09-24): attempted, and blocked — by eval-set size, not by partitioning.**
> The diagnosis below is correct: there is no held-out test set and every published number is
> in-sample. The *remedy* is not. A stratified 40/12/12 partition was built and its minimum
> detectable effect measured (PR #124): **0.2634 on `safety_v1` against a +0.0952 effect**, with
> all 10 metric/contrast pairs underpowered by 1.4–3.1×. Giving the test partition all 64 cases
> still fails on 3 of 5 metrics, and more runs cannot help because ω² is untouched by K.
>
> The finding is larger than this idea: **the full 64-case eval set is already underpowered for
> the effect campaign 09 published.** Decision taken — do not grow the eval set; state the
> limitation instead. See [the plan's stop notice](../plans/2026-09-23-held-out-test-set.md) and
> [the composition measurement](2026-09-23-train-validation-composition.md).
>
> One claim below is also weakened by what followed. "In-sample optimization is a simpler
> explanation for the holdout degradation than Goodharting" remains *possible*, but the cheap
> proxy that could have tested it turned out to be confounded by subset composition — the
> control arm, which cannot overfit, showed the largest train/validation gap. The two
> explanations are still not distinguishable, and now we know they cannot be distinguished on
> this eval set at all.

**The gap.** `sampler_config.json` splits the 64 eval cases **49 train / 15 validation**
(49+15 = 64 exactly). `eval_before` and `eval_after` score **all 64**. So ~77% of every number
this project has published is measured on cases GEPA directly optimized against, and the other
15 were used to select the winning candidate. There is no held-out test set anywhere.

**Why it matters more than it sounds.** The repo's signature finding — *GEPA improves the metrics
in its criteria set and degrades `instruction_following`, the holdout* — is attributed to
metric-Goodharting. **In-sample optimization is a simpler and equally sufficient explanation, and
the two are currently indistinguishable.** Campaign 07 reproduced this across two model families;
it may be reproducing overfitting.

**What the literature does.** A self-improving-pipeline study (pith.science, 2026-09-02) observed
concrete reward hacking — *"a 100% pass rate concealing 68% true capability"* — and proposes two
cheap structural defenses: **frozen holdout partitions never shown to the optimizer**, and
**planted canary cases engineered so that scoring perfectly on them is itself evidence of
cheating**. SAPO (ACM CAIS '26) scores every candidate on a separate holdout via a dedicated
evaluation agent. RoboPhD (arXiv 2026-08-17) shows very small train pools (66–100 examples)
generalizing to much larger held-out sets (267–900) with held-out score rising monotonically, and
credits telling the optimizer explicitly that *"the visible batch is a training signal, not the
target."*

**Build.** A third partition — train / validation / **test** — with test never reaching the
sampler config, plus a handful of planted canary cases. Report the test-set delta as the
headline and the train delta beside it; the gap between them *is* the overfitting measurement.
`build_gepa_criteria` and the sampler configs are the only files that need to know.

**Cost.** Low. The 64 cases are already categorised by `tier` and `category`, so a stratified
split is mechanical. It does shrink the training signal, which argues for generating more cases
rather than re-slicing these.

**You'll know it worked when** the train/test gap is reportable — and if `instruction_following`
degrades on train but holds on test, the Goodhart story was wrong.

---

## Idea 2 — An inference layer, so reports state uncertainty instead of point estimates

**The gap.** `grep -rniE "bootstrap|confidence|p_value|significan" wrangler/reporting/` returns
**zero hits**. Pairing exists only in `campaign_floor.py`. Every published delta in this repo is
a bare point estimate. That is how campaign 09 was filed UNRESOLVED when a per-case paired
contrast showed a clean null.

**What the literature does.** Miller's *Adding Error Bars to Evals* (arXiv 2024-11-01) gives the
two pieces directly: inference on **question-level paired differences** rather than population
means (at per-question correlation 0.5, pairing cuts estimator variance by a third), and an
explicit **minimum-detectable-effect** formula

> δ = (z_α/2 + z_β)·√((ω² + σ²_A/K_A + σ²_B/K_B) / n)

which separates question-sampling variance ω² — reducible **only** by more questions — from
per-question variance σ²/K, reducible by resampling. **This is the exact structural reason DOE 03
measured `score_repeats` exponents near zero for agent-dominated metrics:** resampling cannot
touch ω². Miller's worked example caps resampling at removing 1/3 of variance at K=2 and 2/3 at
K=∞. We rediscovered the ceiling empirically; the formula predicts it.

Bowyer et al. supply the interval method (Wilson / Bayesian beta-binomial, not bootstrap at our n)
and warn that repeated sampling per question creates **clustered** structure that invalidates
ordinary error bars — relevant because `score_repeats: 2` does exactly that. Miller measures
clustered SEs as **over 3× larger** than naive ones on DROP.

**Build.** One module: paired per-case deltas, Wilson/Bayesian intervals, cluster-aware when
`score_repeats > 1`, and **an MDE calculator that runs before a campaign** and refuses to
pre-register a readout the design cannot resolve. That last part would have stopped campaign 09
choosing `safety_v1` as primary.

**Cost.** Low-to-moderate; `bayes_evals` is a drop-in and the per-case data is already in every
stage artifact.

---

## Idea 3 — Race the arms instead of running all of them to a fixed budget

**The gap.** No early stopping: `gepa.optimize` accepts `stop_callbacks` and the repo passes
none. Budget is a flat `max_metric_calls: 600`, so an arm costs ~11h whether it converged at
generation 20 or never. **`gepa.optimize` also takes `seed` (default 0), which this repo has
never set** — every optimize run in the project's history shares one search schedule, so two
"replicates" would not be independent draws. And there is no way to express a replicate at all:
nothing in the manifest schema, `PairFactory` or the DAG can say "run this arm twice," despite
CLAUDE.md requiring it and the campaign 09 reanalysis explicitly needing n=2.

**What the literature does.** Prompt selection under a fixed evaluation budget is exactly
**fixed-budget best-arm identification** (TRIPLE, arXiv 2024-05-30): Sequential Halving and
Continuous Reject beat uniform budget-splitting by 15% and 12%, **and the advantage grows as the
budget shrinks** (9.7–17.4% at only 10 evaluations per prompt). Regret-minimisation (UCB) is the
wrong objective — you want to identify the best arm, not maximise cumulative reward.

Two refinements matter for our case specifically. **SySRs** (arXiv 2026-06-05) augments Successive
Rejects with **paired comparisons** — the same queries across arms — and its guarantees
*strengthen as the arms become more similar*, which is precisely the control-vs-optimized-arm
regime where our floors swamp our effects. **irace** (Operations Research Perspectives, 2016)
formalises racing with **common random numbers** as an explicit variance-reduction device, and
carries the warning that non-elitist racing measurably discards genuinely-best configurations by
ignoring prior-iteration evidence.

On the optimizer's own budget, MO-CAPO's **block-based intensification** (dev set split into
blocks; challengers halted early once dominated) reached **80% of final hypervolume at 32.3% of
budget** versus 90.8% for the unintensified baseline, and cut a first iteration from 2,503k to
296k tokens. Neyman allocation — samples proportional to per-arm standard deviation — cuts
best-of-4 identification cost **48–50%** (arXiv 2026-01-29), and **anytime-valid confidence
sequences** let you stop a comparison as soon as evidence suffices without inflating error.

**Build.** Two separable pieces. (a) A `stop_callbacks` implementation that halts when the
incumbent's improvement is inside the *measured* per-metric floor — the repo now has those floors
and a canary, so the stopping rule can be grounded rather than a patience heuristic. (b) Replicate
support in the manifest (`replicates: 2`, distinct seeds) with a racing allocator across arms.

**Cost.** Moderate — (b) touches `dag.py` and `components.py`, so it busts KFP caches and must
land between campaigns. But this is the idea that makes n=2 affordable, which is the blocker on
every result the repo wants.

---

## Idea 4 — Make the cost-quality tradeoff a real instrument

*(This is the direct answer to "help users understand the cost-quality tradeoff between models.")*

**The gap.** `generate_cost_quality_chart` plots `np.mean(list(before_scores.values()))` — **all
five metrics collapsed into one scalar** — against `blended_cost(model)`, which is **list price**,
with **no uncertainty**. Given that per-metric floors span 3.4× and metrics demonstrably move in
opposite directions, the average hides exactly the tradeoff the chart exists to show. Meanwhile
`measured_cost()` prices real token usage from the eval artifacts and the chart does not use it.

**What the literature does.** HAL (arXiv 2025-10-13, 21,730 rollouts, ~$40k) reports that
cost-accuracy Pareto frontiers are **steep and sparse**, and are **dominated by cheap/low-reasoning
tiers rather than flagship models** — Gemini 2.0 Flash on the frontier for 7 of 9 benchmarks,
DeepSeek R1 for 0 of 9. Its recommended reporting format is **frontier-membership counts per
benchmark, not a pooled score**. It also measures that raising reasoning effort **failed to improve
accuracy in 21 of 36 runs**, and that **model×scaffold interactions are non-additive** — so a
cross-model prompt result is a model×scaffold cell, not a model main effect.

RouteLLM (ICLR 2025) supplies the vocabulary: **CPT** (call-performance threshold — what fraction
of calls must go to the strong model to hit a target quality) and **APGR** (average performance
gap recovered), plus the discipline that any cost-quality claim must be measured against a
**random router under the same cost constraint**. FrugalGPT (arXiv 2023-05-09) supplies the
complementarity argument — on COQA **13% of items are ones GPT-4 gets wrong and GPT-3 gets
right** — which is why a per-case comparison, not a leaderboard, justifies a tier choice.
MO-CAPO gives an implementable cost objective, `c(p,x) = w_in·tok_in + w_out·tok_out` with weights
from per-million prices. Artificial Analysis notes that published costs **exclude judge/grader
inference** — ours should say which convention it uses.

**Build.** Replace the averaged scatter with: per-metric Pareto frontiers using **measured**
cost, points carrying intervals from idea 2, frontier-membership counts per metric, and a
before→after arrow per tier. Then add the question the harness can already answer and nothing
asks: **does optimizing a cheap tier's prompt move it onto or past an expensive tier's frontier
point?** All the data exists. That is the demo-worthy result.

**Cost.** Low. This is reporting over data already in the artifacts.

---

## Idea 5 — Turn the canary into a valid drift test, with ground-truth anchors

**The gap.** The canary (shipped 2026-09-22) freezes responses and re-scores them, which correctly
isolates judge from agent. But it has **no ground-truth labels** and **no sequential test** — it
compares two readings directly, which is the procedure measured to false-alarm on 75% of
drift-free streams. It also cannot distinguish *the judge changed* from *the judge was always
wrong*, because nothing in this repo is anchored to a human label.

**What the literature does.** The judge-drift paper (arXiv 2026-06-13) uses a **fixed
human-labeled anchor set re-scored at a steady interleave**, combined with a **betting e-process**
on the judge-versus-human gap. It identified a judge version bump in **60/60 runs with zero
judge-to-system misattribution**, and correctly attributed a contaminating scoring-prompt change
in 110/120. Cost is **0.21–0.64×** of strong-judging every item. Its design principle — the
anchor process must detect faster than the main monitoring process ("the attribution race") — is
the part our canary is missing.

Two hard results raise the stakes. A best-arm-identification paper (arXiv 2026-01-29) proves that
a selection procedure relying **only** on a biased LLM judge cannot identify the best arm at any
sample size — an instance-wise lower bound of **≥50% error** — and that a **hybrid** design
(cheap judge everywhere, ground-truth audit on a selectively chosen subset) reaches the same
fixed-confidence guarantee at **70–90% lower total cost**, auditing in proportion to the square
root of the residual second moment of judge error. PPI++ (ICML 2025) gives an estimator combining
a small human-labeled set with a large LLM-labeled set that is **provably never worse than the
human-only estimator**, raising effective human sample size by up to ~50%.

On spending the judge budget: **ROBIN-HOOD** (arXiv 2026-02-17) shows variance-adaptive query
allocation reaching uniform-allocation accuracy at **half the budget**, because per-case judge
variance is heterogeneous by orders of magnitude — which we have already measured as a per-case
disagreement rate (0/64 for safety, 64/64 for instruction-following). Artificial Analysis
allocates **1–5 repeats per evaluation by noisiness** rather than uniformly; we apply one
`score_repeats` to everything.

**Build.** (a) Label a small anchor set by hand — a few dozen cases — and interleave it; (b) swap
the canary's direct comparison for a sequential/e-process test; (c) allocate `score_repeats`
per-metric by measured judge variance instead of one global number.

**Cost.** (a) is the only real cost and it is the one thing here that cannot be automated. It is
also the only path to knowing whether our metrics are *right*, as opposed to merely stable.

---

## What I would do first

> **SUPERSEDED 2026-09-24.** Idea 1 was tried first and is blocked (see its outcome note above).
> **Idea 2 is now the front of the queue**, and the reason is stronger than when this was
> written: the MDE gate built for idea 1 is already half of it, and it produced the finding that
> the *existing* published results sit at the boundary of 80% power. The immediate piece is to
> run that calculation at campaign pre-registration rather than after the fact.

~~**Idea 1, then idea 2.** Idea 1 is cheap and every published result depends on it; until there is
a held-out test set, the repo's central finding has two explanations and no way to choose.~~ Idea 2
is the one that converts campaigns from "unresolved" into decisions, and it retroactively improves
every existing artifact because the per-case data is already there.

Idea 4 is the best value per hour of work and the most demo-visible, but it reports on numbers
whose validity ideas 1 and 2 establish — so it should follow them, not lead.

Idea 3 is the highest-impact and the most invasive; it is what makes n=2 replicates affordable,
and it should land in a single between-campaigns window because it busts KFP caches.

Idea 5 is the only one requiring human labels, which makes it the slowest to start and the one
worth starting early for that reason.

## Sources

GEPA — arXiv 2507.19457 (ICLR 2026 Oral) · MO-CAPO — arXiv, 2026-05-15 · Pure-exploration bandits
for prompt selection — arXiv 2605.14553 (ICLR 2026) · TRIPLE — arXiv, 2024-05-30 · SySRs — arXiv,
2026-06-05 · irace — *Operations Research Perspectives* 3 (2016) 43–58 · Miller, *Adding Error
Bars to Evals* — arXiv, 2024-11-01 · Bowyer et al. — arXiv 2025-03-03, ICML 2025 Spotlight ·
judge-drift attribution — arXiv, 2026-06-13 · biased-judge BAI — arXiv, 2026-01-29 · PPI++ /
autoeval — arXiv 2024-03-09, ICML 2025 · ROBIN-HOOD — arXiv, 2026-02-17 · HAL — arXiv, 2025-10-13
· RouteLLM — arXiv 2024-06-26, ICLR 2025 · FrugalGPT — arXiv, 2023-05-09 · τ-bench — arXiv,
2024-06-17 · guardrail-metric framework — arXiv, 2024-02-18 · SAPO — ACM CAIS '26 · RoboPhD —
arXiv, 2026-08-17 · APO survey — ACL Anthology 2025.emnlp-main.1681 · pith.science, 2026-09-02 ·
Artificial Analysis Intelligence Index v4.3.2.

All 25 extracted claims survived three-vote adversarial verification; none were refuted.
