"""GEPA optimization — wraps the ADK GEPARootAgentPromptOptimizer with patches."""

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import DEFAULT_JUDGE_MODEL, DEFAULT_OPTIMIZER_MODEL
from .optimizer_config import build_optimizer_config

log = logging.getLogger(__name__)


def _fmt_elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


class _ToolsetFailureCounter(logging.Handler):
    """Counts the one ADK warning that means "this case ran with no tools".

    `llm_agent._convert_tool_union_to_tools` catches *every* exception from
    `toolset.get_tools()`, logs this warning, and returns `[]`. The agent then
    answers without its tools and GEPA scores the result as a bad prompt — a
    network blip becomes evidence about the instruction. Nothing raises and the
    run does not slow down, so watching the log is the only way to know.

    Counted rather than escalated: one lost toolset in a 100-call budget is
    noise worth reporting, not a reason to throw away the run. The judgement of
    how much is too much belongs to whoever reads the summary.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    # ADK words this warning differently across releases, and matching one
    # spelling makes the counter silently useless. 2.7.1 logged "Failed to get
    # tools from toolset ..."; 2.8.0 logs "Agent <name> will run without the
    # tools from toolset ...". On 2026-09-08 the optimize container was on
    # 2.8.0 while this matched only the 2.7.1 wording, so it reported zero tool
    # losses through a live campaign in which five had occurred.
    #
    # Substring, not startswith: 2.8.0 prefixes the agent name, so the message
    # no longer begins with the phrase.
    _TOOLSET_FAILURE_PHRASES = (
        "Failed to get tools from toolset",  # <= 2.7.1
        "will run without the tools",  # >= 2.8.0
    )

    def emit(self, record):
        message = record.getMessage()
        if any(p in message for p in self._TOOLSET_FAILURE_PHRASES):
            self.count += 1


def _enrich_with_rationales(extracted: dict | None, eval_results: list) -> dict | None:
    """Put the judge's per-rubric reasoning back into GEPA's reflective dataset.

    ADK's `_extract_eval_data` emits `{metric_name, score, eval_status}` per metric and drops
    `EvalMetricResult.details.rubric_scores[].rationale` -- a populated explanation of *why*
    each rubric passed or failed. The reflection model that writes every candidate prompt
    therefore sees numbers and no diagnosis.

    That is the input GEPA's method is built on: the metric's textual feedback is read
    straight into the reflection prompt, and a metric returning only pass/fail starves the
    step. It is also a plausible mechanism for two things measured in this repo and never
    explained -- the criterion improving while the holdout degrades across five arms, and
    prompts growing 78 -> 3,873 characters, which is what a search looks like when it cannot
    see what is wrong and can only add more instructions.

    Enrichment, not replacement: the original dict is mutated in place and returned, so if
    ADK changes shape the worst case is that nothing is added.

    **Every failure mode here is swallowed.** A nine-hour optimize stage must not die because
    an enrichment met a shape it did not expect, and a reflector with no rationale is exactly
    what we have today -- so the downside of silence is the status quo, not a regression.
    """
    if not isinstance(extracted, dict):
        return extracted

    for case in eval_results or []:
        try:
            entry = extracted.get(getattr(case, "eval_id", None))
            if not isinstance(entry, dict):
                continue
            invocations = entry.get("invocations")
            per_invocation = getattr(case, "eval_metric_result_per_invocation", []) or []
            if not isinstance(invocations, list):
                continue
            for inv_dict, inv_obj in zip(invocations, per_invocation, strict=False):
                metric_dicts = inv_dict.get("eval_metric_results")
                metric_objs = getattr(inv_obj, "eval_metric_results", []) or []
                if not isinstance(metric_dicts, list):
                    continue
                for md, mo in zip(metric_dicts, metric_objs, strict=False):
                    details = getattr(mo, "details", None)
                    rubrics = getattr(details, "rubric_scores", None) or getattr(
                        mo, "rubric_scores", None
                    )
                    rationales = [
                        {
                            "rubric_id": getattr(r, "rubric_id", None),
                            "score": getattr(r, "score", None),
                            "rationale": getattr(r, "rationale", None),
                        }
                        for r in (rubrics or [])
                        if getattr(r, "rationale", None)
                    ]
                    # An entry whose rationale is None is noise in a reflection prompt.
                    if rationales and isinstance(md, dict):
                        md["rubric_scores"] = rationales
        except Exception as exc:  # never let enrichment fail a nine-hour stage
            log.warning("could not attach rubric rationales: %s", exc)
    return extracted


# Idempotence marker for _patch_gepa_optimize. A mutable container rather than a
# rebound module global: the flag is set from inside a function, and `global` for
# that is both lint-discouraged and easy to get wrong under re-import.
_GEPA_PATCH_STATE: dict[str, bool] = {"optimize": False}

# Per-run arguments for `gepa.optimize()`, set by `optimize()` and cleared in its
# `finally`. Separate from the patch-idempotence marker above because the lifetimes
# differ: the patch is applied once per process, while these change per run and must
# not leak from one arm into the next when a single container optimizes several.
#
# Read at call time rather than captured at patch time -- `_patch_gepa_optimize` wraps
# `gepa.optimize` once, but the wrapper calls `_gepa_extra_kwargs()` fresh on every
# invocation, so a value set after patching still takes effect.
_GEPA_RUN_KWARGS: dict[str, object] = {}


def _gepa_extra_kwargs() -> dict:
    """Arguments we want on `gepa.optimize()` that ADK's config has nowhere to put.

    `gepa.optimize()` takes 46 arguments and ADK forwards 8. Most of the other 38 are
    observability or tuning surface we do not need. This one is different:

    **`use_merge`** is `False` in the installed gepa, while the published guidance says it
    defaults to `True`. It is injected here rather than configured because
    `GEPARootAgentPromptOptimizerConfig` has no field for it.

    **It is inert in this repo, and kept deliberately.** Merge is *field-wise recombination
    across multiple predictors*: `does_triplet_have_desirable_predictors` needs some
    predictor where one parent is byte-identical to the common ancestor and the other
    differs, so that selecting whole fields means something. `GEPARootAgentPromptOptimizer`
    optimizes **one** predictor (`agent_prompt`), which makes that demand a descendant whose
    prompt equals its ancestor's -- and a descendant exists *because* the prompt was mutated.
    Measured: 34 pairs on a real run, 0 eligible; 19 x `No merge candidates found` and zero
    merges across the m01 stage's 113 generations.

    So the **9.2x-shorter-prompt** result that motivated this does **not** transfer -- it is
    measured on multi-module programs with separate prompts to recombine. Shipping on that
    citation without first checking our predictor count was the mistake; the claim was true
    and about a different configuration.

    Kept on anyway because it costs nothing -- a failed attempt falls through to the
    reflective proposer in the *same* iteration, spending no evaluation budget -- and becomes
    correct automatically if ADK ever optimizes sub-agent instructions too. That, not a gepa
    bump, is the trigger to re-run `scripts/check_merge_eligibility.py`;
    `tests/test_merge_is_inert.py` pins the upstream semantics this rests on.

    **Per-run additions** (`seed`, `stop_callbacks`) come from `_GEPA_RUN_KWARGS`, which
    `optimize()` populates and clears. They are merged over the constants rather than
    under them, because a run that explicitly asks for a seed must get that seed.

    Filtered against the live signature below, so an argument gepa drops in a future
    version degrades to "not passed" instead of a `TypeError` nine hours into a stage.
    """
    return {"use_merge": True, **_GEPA_RUN_KWARGS}


def _patch_gepa_optimize():
    """Inject `_gepa_extra_kwargs()` into ADK's `gepa.optimize()` call.

    Caller-supplied values win, so this raises the floor without overriding anything ADK
    decides to start forwarding. Unknown arguments are dropped against the live signature:
    the failure mode this avoids is a `TypeError` at the single call that costs nine hours.
    """
    import inspect

    import gepa

    if _GEPA_PATCH_STATE["optimize"]:
        return

    _orig_optimize = gepa.optimize
    accepted = set(inspect.signature(_orig_optimize).parameters)

    def _patched_optimize(*args, **kwargs):
        for key, value in _gepa_extra_kwargs().items():
            if key in accepted and key not in kwargs:
                kwargs[key] = value
        return _orig_optimize(*args, **kwargs)

    # ty: monkey-patching a precisely-typed module function is the point here; the
    # wrapper deliberately has a looser signature so it can forward anything.
    gepa.optimize = _patched_optimize  # ty: ignore[invalid-assignment]
    _GEPA_PATCH_STATE["optimize"] = True


def _patch_adk(forward_rationale: bool = True):
    """Apply ADK patches for GEPA compatibility.

    Verified against google-adk 2.8.0 on 2026-09-08 (and 2.7.1 on 2026-08-20).

    Patch 1/2 — eval_case/eval_set extra="forbid" (issue #5906). Issue is
        CLOSED but extra="forbid" is still present on 8 classes at 2.7.1.
        Still required.
    Patch 3 — LocalEvalService null guard (issue #6071). Issue CLOSED
        2026-08-06 but the fix is NOT in the 2.7.1 or 2.8.0 release. Still
        required; re-checked at 2.8.0 and the null guard is still absent.
    Patch 4 — LocalEvalSampler score coercion + logging. Local
        instrumentation, not an upstream workaround.
    Patch 5 — REMOVED. Upstream fixed #6072 in 2.7.1 and went further
        (rubric_id matching). Keeping it was a regression.
    Patch 6 — SafetyEvaluatorV1 metric version pin. ADK asks for the
        *unversioned* safety metric; the SDK resolves that client-side to
        safety_v3, which us-central1 does not serve. Still required.
    Patch 8 — NOT applied here. `_deferred_toolset_closes()` is a run-scoped
        context manager rather than a process-wide patch, because outside the
        optimize window `McpToolset.close()` must stay a real close. Applied at
        the call site in `optimize()`; see that docstring for the mechanism.

    Re-run the probe in docs/notes/adk-patch-status.md on every ADK bump.
    """
    _patch_gepa_optimize()

    from google.adk.evaluation import eval_case as _ec
    from google.adk.evaluation import eval_set as _es

    for _mod in (_ec, _es):
        for _name in dir(_mod):
            _cls = getattr(_mod, _name)
            if isinstance(_cls, type) and hasattr(_cls, "model_config"):
                try:
                    if _cls.model_config.get("extra") == "forbid":
                        _cls.model_config["extra"] = "ignore"
                        _cls.__pydantic_complete__ = False
                except (TypeError, AttributeError):
                    pass
    for _mod in (_ec, _es):
        for _name in dir(_mod):
            _cls = getattr(_mod, _name)
            if isinstance(_cls, type) and hasattr(_cls, "model_rebuild"):
                # Not every ADK model can be rebuilt; the ones that can't are
                # already resolved, so a failure here is not actionable.
                with contextlib.suppress(Exception):
                    _cls.model_rebuild(force=True)

    from google.adk.evaluation import local_eval_service as les

    _orig = les.LocalEvalService._evaluate_single_inference_result

    async def _patched(self, inference_result, evaluate_config):
        if inference_result.inferences is None:
            from google.adk.evaluation.eval_result import EvalCaseResult, EvalStatus

            return inference_result, EvalCaseResult(
                eval_id=inference_result.eval_case_id,
                eval_set_id=inference_result.eval_set_id,
                final_eval_status=EvalStatus.NOT_EVALUATED,
                overall_eval_metric_results=[],
                eval_metric_result_per_invocation=[],
                session_id="skipped",
            )
        return await _orig(self, inference_result=inference_result, evaluate_config=evaluate_config)

    les.LocalEvalService._evaluate_single_inference_result = _patched

    from google.adk.optimization import local_eval_sampler as sampler_mod

    _orig_extract = sampler_mod.LocalEvalSampler._extract_eval_data

    def _patched_extract(self, eval_set_id, eval_results):
        metric_totals = {}
        metric_counts = {}
        rubric_failures = {}

        for case_result in eval_results:
            for inv in getattr(case_result, "eval_metric_result_per_invocation", []):
                for mr in getattr(inv, "eval_metric_results", []):
                    if mr.score is None:
                        mr.score = 0.0
                    name = getattr(mr, "metric_name", "unknown")
                    metric_totals[name] = metric_totals.get(name, 0.0) + mr.score
                    metric_counts[name] = metric_counts.get(name, 0) + 1
                    if hasattr(mr, "rubric_scores") and not mr.rubric_scores:
                        rubric_failures[name] = rubric_failures.get(name, 0) + 1

        if metric_totals:
            breakdown = " | ".join(
                f"{n.split('/')[-1][:25]}={metric_totals[n] / metric_counts[n]:.2f}"
                for n in sorted(metric_totals)
            )
            log.info("Eval batch (%d cases): %s", len(eval_results), breakdown)

        if rubric_failures:
            for metric, count in rubric_failures.items():
                log.warning(
                    "RUBRIC MATCH FAILURE: %s had %d/%d cases with no rubric scores",
                    metric,
                    count,
                    len(eval_results),
                )

        extracted = _orig_extract(self, eval_set_id, eval_results)
        if not forward_rationale:
            # OFF means UPSTREAM ADK BEHAVIOUR, not a softer version of our patch. If this
            # still routed through _enrich_with_rationales and merely skipped the attach,
            # campaign 09's contrast would measure our wrapper rather than the rationale.
            return extracted
        # Patch 4b -- put the judge's reasoning back. See _enrich_with_rationales.
        return _enrich_with_rationales(extracted, eval_results)

    sampler_mod.LocalEvalSampler._extract_eval_data = _patched_extract

    # Patch 6 — pin GEPA's safety metric to v1.
    #
    # SafetyEvaluatorV1.evaluate_invocations() asks the Vertex eval SDK for the
    # *unversioned* PrebuiltMetric.SAFETY. The SDK resolves that client-side
    # through METRIC_LATEST_SPEC_NAME, which maps "safety" -> "safety_v3" — a
    # version us-central1 does not serve. Every case then comes back
    # `400 INVALID_ARGUMENT: Unsupported predefined metric: safety_v3`, the
    # score lands as None, and patch 4 coerces it to 0.0. GEPA does not fail;
    # it optimizes against a criterion that is pinned at zero.
    #
    # Same client-ahead-of-server mismatch as silent failure #3 in
    # docs/notes/silent-failures.md. Batch eval was pinned to v1 when that was
    # found; GEPA's criteria were not, because the version is chosen inside ADK
    # and never appears in sampler_config.json (which correctly says
    # "safety_v1"). `PrebuiltMetric.SAFETY_V1` falls through the loader's
    # __getattr__ to a bare-name lookup and resolves to "safety_v1", so no
    # private SDK import is needed.
    from google.adk.dependencies.vertexai import vertexai as _vertexai
    from google.adk.evaluation import safety_evaluator as safety_mod
    from google.adk.evaluation import vertex_ai_eval_facade as facade_mod

    _pinned_safety = _vertexai.types.PrebuiltMetric.SAFETY_V1
    try:
        _resolved = _pinned_safety._get_api_metric_spec_name()
    except AttributeError:  # the SDK reorganised its metric loader
        _resolved = None

    if _resolved == "safety_v1":

        def _patched_safety(
            self, actual_invocations, expected_invocations=None, conversation_scenario=None
        ):
            # Resolved off the module, not captured at patch time, so the
            # facade stays substitutable (and follows ADK if it swaps the
            # class out).
            return facade_mod._SingleTurnVertexAiEvalFacade(
                threshold=self._threshold,
                metric_name=_pinned_safety,
            ).evaluate_invocations(actual_invocations, expected_invocations, conversation_scenario)

        safety_mod.SafetyEvaluatorV1.evaluate_invocations = _patched_safety
    else:
        # Pinning to a name the SDK cannot resolve would be worse than leaving
        # ADK alone, so bail out loudly instead.
        log.warning(
            "Patch 6 skipped: PrebuiltMetric.SAFETY_V1 resolves to %r, not 'safety_v1'. "
            "safety_v1 criteria may fail with 'Unsupported predefined metric'. "
            "Re-run the probe in docs/notes/adk-patch-status.md.",
            _resolved,
        )

    log.info("ADK patches applied (1-4, 6; patch 5 removed — upstream #6072 fixed in ADK 2.7.1)")


def _create_wrapper_module(agent_module_path: str, temp_dir: str) -> str:
    """Create a temporary wrapper that exposes the agent as root_agent."""
    wrapper_dir = os.path.join(temp_dir, "wrapper")
    os.makedirs(wrapper_dir, exist_ok=True)

    init_content = f"""
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location("_agent", "{agent_module_path}/__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root_agent = mod.agent.root_agent
agent = types.SimpleNamespace(root_agent=root_agent)
"""
    with open(os.path.join(wrapper_dir, "__init__.py"), "w") as f:
        f.write(init_content)

    return wrapper_dir


async def _prewarm_mcp_toolsets(agent, tag: str = "  ", max_retries: int = 3) -> int:
    """Pre-warm MCP tool sessions so GEPA doesn't timeout on first connection.

    Retries with exponential backoff to survive Cloud Run cold starts.
    Returns the number of toolsets successfully warmed.
    """
    from google.adk.tools.base_toolset import BaseToolset

    mcp_toolsets = [t for t in agent.tools if isinstance(t, BaseToolset)]
    warmed = 0
    for ts in mcp_toolsets:
        for attempt in range(max_retries):
            try:
                tools = await ts.get_tools()
                log.info("Pre-warmed %s: %d tools", type(ts).__name__, len(tools))
                warmed += 1
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    log.warning(
                        "MCP pre-warm attempt %d/%d failed, retrying in %ds: %s",
                        attempt + 1,
                        max_retries,
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    log.exception(
                        "MCP pre-warm failed after %d attempts for %s",
                        max_retries,
                        type(ts).__name__,
                    )
    if mcp_toolsets:
        print(f"{tag}  Pre-warmed {warmed}/{len(mcp_toolsets)} MCP toolset(s)", flush=True)
        if warmed < len(mcp_toolsets):
            print(
                f"{tag}  WARNING: {len(mcp_toolsets) - warmed} toolset(s) failed — "
                f"optimization will proceed with reduced tool context",
                flush=True,
            )
    return warmed


@dataclass
class _ToolsetClosePatch:
    """State for patch 8. A dataclass rather than a dict so the fields stay typed."""

    depth: int = 0  # nesting depth; only the outermost window patches and flushes
    orig: Any = None  # the real McpToolset.close, restored on exit
    # Identity-keyed: two toolsets may compare equal, and closing one of them twice
    # while never closing the other is the bug this patch exists to prevent.
    pending: dict[int, Any] = field(default_factory=dict)
    deferred: int = 0  # how many closes were swallowed, for the run log

    def reset(self) -> None:
        self.depth, self.orig, self.pending, self.deferred = 0, None, {}, 0


_TOOLSET_CLOSE_PATCH = _ToolsetClosePatch()


def _toolset_class_for_patching():
    """The class whose close() is deferred. Indirected so tests can substitute a fake."""
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    return McpToolset


def _reset_toolset_close_patch_for_tests() -> None:
    """Restore the patch state to its baseline. Tests only."""
    _TOOLSET_CLOSE_PATCH.reset()


@contextlib.asynccontextmanager
async def _deferred_toolset_closes(tag: str = "  "):
    """Patch 8 — stop a runner's close() tearing down a toolset other candidates share.

    This is the fix for silent failure #12, open since 2026-09-08 and localised by
    measurement on 2026-09-12. The mechanism, end to end:

    1. `agent.clone()` is a **shallow** copy, so every GEPA candidate shares **one**
       `McpToolset` per server. CLAUDE.md already records this for the tool-list cache;
       it is equally true of the session.
    2. GEPA drives a short-lived `Runner` per candidate evaluation. `Runner.close()` calls
       `_cleanup_toolsets`, which calls `toolset.close()` on everything it collects --
       **1,812 closes in one 572-minute stage**, arriving in bursts of ~32.
    3. `McpToolset.close()` clears the tool-list cache and closes the session manager. So
       one candidate's teardown strands another candidate's in-flight `list_tools()`.
    4. The victim blocks for the full 120s `MCP_TIMEOUT_SECONDS`, then
       `TimeoutError -> CancelledError -> ConnectionError` with an empty message, because
       `str(CancelledError())` is `""`. ADK hands the agent **zero tools**, it scores near
       zero on tool use, and that score enters the objective GEPA is searching.

    Deferring close() removes the precondition rather than narrowing the race: with no
    teardown mid-run there is nothing to strand, whatever the concurrency and whatever the
    cache does. The sessions are still closed -- once each, on exit -- so this defers
    teardown, it does not skip it.

    **Three fixes shipped for #12 before this one and the rate did not move** (14% -> 15%
    -> 12% and 24%). Two of them passed their own tests, so a green suite here means very
    little: **the acceptance test is the `will run without the tools` rate on a real
    optimize stage**, counted with `scripts/analyze_toolset_loss.py`, expected 0. The
    deferred-close count printed on exit is the direct check that this window was actually
    in force -- a stage reporting 0 deferrals did not exercise this patch at all, and its
    clean result means nothing.

    Scoped to the optimize run and reversed on exit, including on the error path: outside
    the window `close()` must be a real close, or the pipeline container leaks sessions.
    """
    cls = _toolset_class_for_patching()
    state = _TOOLSET_CLOSE_PATCH
    state.depth += 1
    outermost = state.depth == 1

    if outermost:
        state.orig = cls.close
        state.pending = {}
        state.deferred = 0

        async def _deferred_close(self) -> None:
            """Record the request and return. The real close happens once, on exit."""
            state.pending[id(self)] = self
            state.deferred += 1

        cls.close = _deferred_close

    try:
        yield
    finally:
        state.depth -= 1
        if outermost:
            cls.close = state.orig
            deferred = state.deferred
            to_close = list(state.pending.values())
            state.reset()
            for ts in to_close:
                # Never let a teardown failure propagate: this runs in a finally, so
                # raising here would replace whatever the optimize run was already
                # failing with -- and the remaining toolsets would leak.
                try:
                    await ts.close()
                except Exception:
                    log.warning("Deferred close failed for %s", type(ts).__name__, exc_info=True)
            if deferred:
                print(
                    f"{tag}  Deferred {deferred} toolset close(s) across "
                    f"{len(to_close)} shared toolset(s); closed them once at teardown "
                    f"(silent-failures #12)",
                    flush=True,
                )


def _apply_model_override(root_agent, model: str, tag: str = "") -> None:
    """Point the loaded agent at ``model``, if one was named.

    GEPA optimizes whatever agent the ``_opt`` module builds, and that module
    reads its model from ``config.py`` -- so without this a manifest pair
    declaring ``model: claude-sonnet-5`` would deploy sonnet-5 and optimize
    sonnet-4-6, then label the result sonnet-5. Nothing failed when that
    happened; the number was just about a different model than its label.

    Routed through ``resolve_model`` for the same reason every other call site
    is: a bare Claude id is not servable, it needs the global resource path.
    An unregistered id raises rather than falling back to the module's model,
    because falling back is exactly the silent substitution being fixed.
    """
    if not model:
        return
    from ..core.config import resolve_model
    from ..core.models import get_spec

    # Validate before resolving. `resolve_model` does not raise on an unknown
    # id -- it falls through to the Gemini branch and hands back
    # Gemini(model="definitely-not-a-model"), so a typo would quietly optimize
    # a nonexistent Gemini model instead of the Claude one intended.
    get_spec(model)
    root_agent.model = resolve_model(model)
    print(f"{tag}  Model override: {model} (from manifest)", flush=True)


def gepa_run_dir(agent_module_path: str) -> Path:
    """Where GEPA writes its candidates, per-candidate scores and search tree.

    One definition, because two callers need it: `optimize()` passes it to GEPA, and the
    KFP optimize component uploads what GEPA leaves there. Recomputing the expression in
    the component would let the two drift, and the failure would be silent -- the upload
    would find an empty directory and log nothing interesting.

    What lands here is the only per-candidate record of a run. `gepa_state.bin` holds every
    candidate prompt alongside its validation subscores -- 14 candidates spanning 78 to
    12,741 characters on the one surviving local run -- against the single (prompt, delta)
    pair the stage artifact records. `candidates.json` has the prompts as plain JSON but
    **no scores**, so anything correlating length against score needs the pickle.
    """
    return Path("outputs") / "gepa_runs" / Path(agent_module_path).name


def seed_for_arm(agent_name: str) -> int:
    """A stable per-arm seed for `gepa.optimize()`.

    **Why derive rather than plumb.** `components.py` already passes the pair id in as
    `agent_name`, so computing the seed here keeps the whole replicate feature out of
    the KFP component bodies — and a change to a component body busts its cache and, per
    silent-failures #13, cannot merge under a live driver. Nothing about the seed needs
    to cross that boundary.

    **md5, not `hash()`.** `hash()` on a str is salted by `PYTHONHASHSEED`, so it differs
    between processes; two stages of one campaign would then disagree about an arm's
    seed, and a "replicate" would be unreproducible for the opposite reason to the one we
    are fixing.

    Until this existed, every optimize run in the project's history used gepa's default
    `seed=0`, so two replicates of an arm would have shared a minibatch schedule and been
    less independent than they looked. Distinct pair ids now give distinct schedules,
    which is what makes `replicates:` produce genuine draws.
    """
    import hashlib

    digest = hashlib.md5(agent_name.encode(), usedforsecurity=False).hexdigest()
    return int(digest[:8], 16)


def optimize(
    agent_module_path: str,
    evalset_path: str | None = None,
    sampler_config_path: str | None = None,
    eval_data_path: str | None = None,
    agent_name: str = "",
    eval_thresholds: dict[str, float] | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    max_metric_calls: int | None = None,
    initial_instruction: str | None = None,
    model: str = "",
    forward_rationale: bool = True,
    patience: int | None = None,
    continuous_val_score: bool = False,
) -> str:
    """Run GEPA optimization. Returns the optimized instruction string.

    Args:
        agent_module_path: Path to agent wrapper module (must export agent.root_agent)
        evalset_path: Path to evalset JSON (ignored if sampler_config_path is set)
        sampler_config_path: Path to sampler config JSON file
        eval_data_path: Path to simplified eval YAML (for auto-generating GEPA evalset)
        agent_name: Display name for logging (e.g. "lite-gemini-3.1-flash-lite")
        eval_thresholds: Per-metric thresholds used ONLY for the fallback criteria
            built when no sampler_config_path is given. When a sampler_config.json
            exists it is authoritative and these are ignored.
        judge_model: Judge model for eval metrics
        patience: Stop after this many GEPA iterations without an improvement in the
            best validation score. `None` (the default) passes no stopper and behaves
            exactly as before. Opt-in because it re-baselines an arm's budget: a
            campaign run with a patience is not budget-comparable to one without.
        continuous_val_score: Score each case on the mean of its metrics' continuous
            scores instead of ADK's `1.0 if PASSED else 0.0`. Off by default. Opt-in
            because it changes what GEPA selects on, which is a comparability boundary
            of the same kind as the 2026-09-17 judge re-baseline. See
            `wrangler/optimize/continuous_score.py`.
    """
    tag = f"  [{agent_name}] " if agent_name else "  "
    print(f"{tag}[1/3] Applying ADK patches...", flush=True)
    _patch_adk(forward_rationale=forward_rationale)

    import vertexai

    from ..core.config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
    from google.adk.optimization.gepa_root_agent_prompt_optimizer import (
        GEPARootAgentPromptOptimizer,
        GEPARootAgentPromptOptimizerConfig,
    )
    from google.adk.optimization.local_eval_sampler import (
        LocalEvalSampler,
        LocalEvalSamplerConfig,
    )

    print(f"{tag}[2/3] Loading agent and configs...", flush=True)
    agent_module_path = str(Path(agent_module_path).resolve())
    init_file = os.path.join(agent_module_path, "__init__.py")
    if not os.path.exists(init_file):
        raise FileNotFoundError(
            f"Agent module not found: {init_file}\n"
            f"  The optimizer expects a directory with an __init__.py that exports agent.root_agent.\n"
            f"  Directory contents: {os.listdir(agent_module_path) if os.path.isdir(agent_module_path) else 'NOT A DIRECTORY'}"
        )

    spec = importlib.util.spec_from_file_location("agent_mod", init_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load a Python module from {init_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_mod"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise RuntimeError(
            f"Failed to import agent module at {init_file}: {e}\n"
            f"  Common causes:\n"
            f"    - Missing dependencies (check imports in __init__.py)\n"
            f"    - Relative imports that don't resolve (use absolute paths or sys.path)"
        ) from e

    root_agent = None
    if hasattr(module, "agent") and hasattr(module.agent, "root_agent"):
        root_agent = module.agent.root_agent
    elif hasattr(module, "root_agent"):
        root_agent = module.root_agent

    if root_agent is None:
        exports = [k for k in dir(module) if not k.startswith("_")]
        raise ValueError(
            f"Could not find root_agent in {agent_module_path}\n"
            f"  Module exports: {exports}\n"
            f"  Expected: agent.root_agent (SimpleNamespace) or root_agent (LlmAgent)"
        )

    if initial_instruction:
        root_agent.instruction = initial_instruction
        print(
            f"{tag}  Instruction override: {len(initial_instruction)} chars (from manifest)",
            flush=True,
        )
    else:
        print(
            f"{tag}  Instruction: {len(root_agent.instruction)} chars (from _opt module)",
            flush=True,
        )
    # Before anything reads the agent: the model must match the manifest, or the
    # whole run is attributed to the wrong one.
    _apply_model_override(root_agent, model, tag)
    print(f"{tag}  Agent: {root_agent.name} | model: {root_agent.model}", flush=True)

    app_name = os.path.basename(agent_module_path)
    agents_dir = os.path.dirname(agent_module_path)

    if sampler_config_path:
        # sampler_config.json is the single source of truth for GEPA criteria
        # and thresholds. It is used verbatim — experiment eval_thresholds do NOT
        # override it (they only seed the fallback criteria below when no file exists).
        import json as _json

        with open(sampler_config_path) as f:
            sampler_config = _json.load(f)
    else:
        from ..core.converter import build_gepa_criteria

        evalset_stem = Path(evalset_path).stem if evalset_path else "eval_set"
        evalset_stem = evalset_stem.removesuffix(".evalset")
        sampler_config = {
            "eval_config": {
                "criteria": build_gepa_criteria(eval_thresholds, judge_model),
            },
            "app_name": app_name,
            "train_eval_set": evalset_stem,
        }

    try:
        sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
    except Exception as e:
        raise ValueError(
            f"Invalid sampler config: {e}\n"
            f"  Config: {json.dumps(sampler_config, indent=2) if 'json' in dir() else sampler_config}\n"
            f"  If using a sampler_config.json, check that app_name and train_eval_set match your directory structure."
        ) from e

    if sampler_cfg.app_name != app_name:
        sampler_cfg.app_name = app_name

    evalset_dir = os.path.join(agents_dir, app_name)
    evalset_files = (
        [f for f in os.listdir(evalset_dir) if f.endswith(".evalset.json")]
        if os.path.isdir(evalset_dir)
        else []
    )
    if not evalset_files and eval_data_path:
        print(
            f"{tag}  No evalset files in {evalset_dir} — auto-generating from {eval_data_path}",
            flush=True,
        )
        from ..core.converter import generate_gepa_evalset, generate_sampler_config, load_eval_file

        cases = load_eval_file(eval_data_path)
        eval_set_id = f"{app_name}_eval_set"
        generate_gepa_evalset(cases, evalset_dir, eval_set_id=eval_set_id, app_name=app_name)
        if not sampler_config_path:
            generate_sampler_config(app_name, eval_set_id, output_dir=evalset_dir)
            sampler_config_path_auto = os.path.join(evalset_dir, "sampler_config.json")
            with open(sampler_config_path_auto) as f:
                sampler_config = json.load(f)
            sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
            if sampler_cfg.app_name != app_name:
                sampler_cfg.app_name = app_name
        print(f"{tag}  Auto-generated evalset at {evalset_dir}", flush=True)
    elif not evalset_files:
        log.warning(f"No .evalset.json files found in {evalset_dir}. GEPA may fail.")
        print(f"{tag}  WARNING: No evalset files in {evalset_dir}", flush=True)
        print(
            f"{tag}  Run: wrangler generate-evalset --from <eval.yaml> --output {evalset_dir}",
            flush=True,
        )

    run_dir = str(gepa_run_dir(agent_module_path))
    os.makedirs(run_dir, exist_ok=True)
    # optimizer_model is passed explicitly rather than left to ADK's default.
    # ADK's was gemini-2.5-flash, which retires 2026-10-16 and is invisible to the
    # registry's retirement guard because the repo never named the role. See
    # DEFAULT_OPTIMIZER_MODEL in core/models.py for why this id and what it costs.
    optimizer_config = GEPARootAgentPromptOptimizerConfig(
        run_dir=run_dir,
        optimizer_model=DEFAULT_OPTIMIZER_MODEL,
        # ADK's default model_configuration is Gemini-shaped and 400s on Claude
        # Opus 4.7+; see optimizer_config.py for the probe that established it.
        model_configuration=build_optimizer_config(DEFAULT_OPTIMIZER_MODEL),
    )
    # Assigned rather than passed so ADK keeps ownership of the default when the
    # manifest omits it. Keyword args, not a **dict: a heterogeneous kwargs dict
    # collapses to one union type and the config's own field types stop being checked.
    if max_metric_calls is not None:
        optimizer_config.max_metric_calls = max_metric_calls
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    sampler = LocalEvalSampler(sampler_cfg, eval_sets_manager)
    optimizer = GEPARootAgentPromptOptimizer(optimizer_config)

    train_count = len(sampler.get_train_example_ids())
    val_count = len(sampler.get_validation_example_ids())
    max_calls = optimizer_config.max_metric_calls
    print(f"{tag}[3/3] Running GEPA optimization...", flush=True)
    print(
        f"{tag}  Train: {train_count} cases, Val: {val_count} cases, Max metric calls: {max_calls}",
        flush=True,
    )
    print(
        f"{tag}  Optimizer model: {optimizer_config.optimizer_model} (writes prompts) | "
        f"judge: {judge_model} (scores them)",
        flush=True,
    )
    print(f"{tag}  Run dir: {run_dir}", flush=True)

    t0 = time.time()
    # ADK downgrades "the agent lost its toolset" to a warning and carries on,
    # so hook the logger it warns through for the duration of the run.
    toolset_failures = _ToolsetFailureCounter()
    adk_agent_log = logging.getLogger("google_adk.google.adk.agents.llm_agent")
    adk_agent_log.addHandler(toolset_failures)

    # Per-run arguments ADK has nowhere to put. Set here rather than at patch time
    # because the patch is applied once per process while these are per arm; cleared in
    # the `finally` so one arm's seed cannot leak into the next in a shared container.
    _GEPA_RUN_KWARGS.clear()
    if agent_name:
        _GEPA_RUN_KWARGS["seed"] = seed_for_arm(agent_name)
        print(f"{tag}  GEPA seed: {_GEPA_RUN_KWARGS['seed']} (derived from arm id)", flush=True)
    if patience is not None:
        from gepa.utils.stop_condition import NoImprovementStopper

        # `max_metric_calls` stays the ceiling; this can only stop earlier. Measured on
        # campaign 09: patience 15 returns a byte-identical prompt on both arms while
        # spending 62% fewer metric calls, because gepa's `best_idx` is the FIRST argmax
        # and both runs saturated their 15-case validation subset early.
        # docs/analysis/2026-09-24-stopping-replay.md
        _GEPA_RUN_KWARGS["stop_callbacks"] = NoImprovementStopper(patience)
        print(f"{tag}  Early stopping: patience {patience} iterations", flush=True)
    if continuous_val_score:
        print(
            f"{tag}  Continuous per-case scoring ON — GEPA selects on metric means, "
            f"not pass/fail. Not comparable with runs scored the old way.",
            flush=True,
        )

    try:

        async def _run_with_warmup():
            # Patch 8. The window wraps the pre-warm too: the pre-warm itself never
            # closes anything, but a Runner from a previous stage in the same process
            # could, and the first generation is exactly when the sessions are newest.
            async with _deferred_toolset_closes(tag):
                return await _optimize_within_close_window()

        async def _optimize_within_close_window():
            await _prewarm_mcp_toolsets(root_agent, tag)

            # Patch the sampler purely to number the generations in the log. It
            # used to refresh MCP sessions here as well; see the note below.
            _orig_sample = sampler.sample_and_score
            _gen_count = [0]

            # Option A: recover the continuous per-case score ADK collapses to pass/fail.
            # `_evaluate_agent` is the single funnel -- `sample_and_score` calls it exactly
            # once and awaits it before scoring -- so stashing its result here is enough,
            # and it works regardless of `capture_full_eval_data`, which `_extract_eval_data`
            # (patch 4b's hook) depends on and which GEPA does not always set.
            _last_results: list = []
            _orig_evaluate = sampler._evaluate_agent

            async def _capturing_evaluate(*a, **kw):
                results = await _orig_evaluate(*a, **kw)
                _last_results.clear()
                _last_results.extend(results or [])
                return results

            if continuous_val_score:
                sampler._evaluate_agent = _capturing_evaluate  # ty: ignore[invalid-assignment]

            async def _refreshed_sample(candidate, *args, **kwargs):
                _gen_count[0] += 1
                gen = _gen_count[0]
                gen_t0 = time.time()

                # No per-generation session refresh. It used to close every MCP
                # toolset here and re-warm it, and that is what silent-failures #12
                # turned out to be: all 17 of campaign 07's toolset losses began
                # 6-27s after a re-warm, and the failure rate tracked the refresh
                # rather than anything on the servers, which logged 3,626 x 200 OK
                # and zero errors.
                #
                # The refresh was written for a Cloud Run idle drop, and neither half
                # of that applies. CLAUDE.md already records the ~2 minute drop as
                # unreproducible -- observed once from inside the pipeline container
                # and never since -- and the optimize stage does not talk to Cloud Run
                # at all: it starts local FastMCP servers on localhost and overrides
                # the MCP URLs to point at them.
                #
                # Meanwhile ADK 2.8.0 pools sessions properly: a 900s idle TTL against
                # our ~286s between generations, so nothing idles out in the gap, and its
                # idle sweep skips any session that has a call in flight. Our
                # close() had no such guard, and it detaches teardowns that are only
                # awaited if they belong to the current loop -- so a teardown from one
                # generation could still be running while the next generation's
                # pre-warm rebuilt the same pool key. Removing the refresh removes
                # that race with it.
                #
                # The single pre-warm before the run stays; it is what makes the first
                # generation cheap. See docs/notes/silent-failures.md #12.
                print(f"{tag}  Generation {gen}: evaluating candidate...", flush=True)

                result = await _orig_sample(candidate, *args, **kwargs)

                if continuous_val_score and _last_results:
                    from .continuous_score import apply_continuous_scores

                    result.scores = apply_continuous_scores(result.scores, _last_results)

                gen_elapsed = time.time() - gen_t0
                print(f"{tag}  Generation {gen}: scored in {gen_elapsed:.1f}s", flush=True)
                return result

            # ADK patch: wraps the bound method with per-generation MCP refresh.
            sampler.sample_and_score = _refreshed_sample  # ty: ignore[invalid-assignment]

            return await optimizer.optimize(root_agent, sampler)

        optimization_result = asyncio.run(_run_with_warmup())
    except Exception as e:
        error_msg = str(e)
        if "ValidationError" in type(e).__name__ or "validation" in error_msg.lower():
            raise RuntimeError(
                f"GEPA optimization failed with validation error: {e}\n"
                f"  Common causes:\n"
                f"    - Evalset JSON has fields GEPA doesn't expect (check .evalset.json format)\n"
                f"    - Tool names in evalset don't match agent's actual tool names\n"
                f"    - Run: wrangler inspect <agent_dir> to see correct tool names"
            ) from e
        raise
    finally:
        adk_agent_log.removeHandler(toolset_failures)
        _GEPA_RUN_KWARGS.clear()

    if toolset_failures.count:
        print(
            f"{tag}  WARNING: {toolset_failures.count} agent invocation(s) ran with a "
            f"missing toolset and were scored anyway — those cases judged a toolless "
            f"agent, not the prompt",
            flush=True,
        )

    best_idx = optimization_result.gepa_result["best_idx"]
    best_agent = optimization_result.optimized_agents[best_idx]
    optimized_instruction = best_agent.optimized_agent.instruction
    n_candidates = optimization_result.gepa_result.get("num_candidates", "?")
    total_calls = optimization_result.gepa_result.get("total_metric_calls", "?")

    scores_summary = ""
    for i, agent_with_score in enumerate(optimization_result.optimized_agents):
        marker = " <-- best" if i == best_idx else ""
        scores_summary += (
            f"\n{tag}    variant {i}: score={agent_with_score.overall_score:.3f}{marker}"
        )

    print(f"{tag}  Optimization complete ({_fmt_elapsed(t0)})", flush=True)
    print(f"{tag}  Candidates: {n_candidates}, Total metric calls: {total_calls}", flush=True)
    print(f"{tag}  Variant scores:{scores_summary}", flush=True)
    print(f"{tag}  Best variant: {best_idx} ({len(optimized_instruction)} chars)", flush=True)

    stderr_path = os.path.join(run_dir, "run_log_stderr.txt")
    if os.path.exists(stderr_path):
        with open(stderr_path) as f:
            rubric_warnings = [line for line in f if "not found in the rubrics" in line]
        if rubric_warnings:
            print(
                f"{tag}  WARNING: {len(rubric_warnings)} rubric match failures during optimization",
                flush=True,
            )
            seen = set()
            for w in rubric_warnings:
                short = w.strip()[:120]
                if short not in seen:
                    print(f"{tag}    {short}", flush=True)
                    seen.add(short)

    return optimized_instruction
