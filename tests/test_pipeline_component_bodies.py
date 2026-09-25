"""Execute the KFP component bodies, rather than reading their source.

`components.py` runs a campaign for 10-24 hours unattended and was the least-tested file in
the repo -- **642 statements at 2%**. Every test of it read `inspect.getsource(...)` and
asserted on substrings, which executes nothing and so bought no coverage. Worse, a source
assertion breaks on a rename while passing through a real defect: `"labels=" in src` is
satisfied by `labels=None`.

These tests call `comp.python_func(...)` against the fakes in
`tests/pipeline_component_harness.py`. KFP's isolation rule governs what a component can
reach *inside the container*; it places no restriction on a test in this repo calling the
function.

**What is deliberately NOT tested here:** process lifecycle -- MCP server startup, the
`try/finally` that uploads server logs, and the `_deferred_toolset_closes` window. Those are
why the optimize component exists as a container, they are covered by
`test_toolset_close_deferral.py`, and faking `subprocess.Popen` well enough to be meaningful
would test the fake. The optimize tests below drive the control-arm path, which returns
before any of that.

**These tests were mutation-checked**, because a test that passes against broken code is
worse than no test and this file exists because the previous ones did exactly that. Each
invariant was broken in a scratch copy of `components.py` and the matching test confirmed to
fail: 46 mutations across eight rounds, all caught as the suite now stands.

**Four of those rounds found a weak test rather than confirming a strong one** -- which is
the whole argument for doing it:

- `test_the_phase_selects_the_stage_directory` passed against a body that hardcoded
  `stage_name = "eval_before"`, because it only ever ran the *before* phase. Parametrised
  over both phases now, so it can see a lost after-side.
- `generate_analysis` has **two** nested `except Exception` guards; narrowing either alone
  leaves the other absorbing the failure. There is a separate test per guard, because a
  single mutation proves nothing about which one is load-bearing.
- the run_dir shipping test was **vacuous**: `run_dir_path.is_dir()` is false on a dev
  machine, so the loop it claimed to test never ran. It now builds a real run_dir.
- `forward_rationale` was only ever asserted with the key explicitly set, so a flipped
  *default* -- which would silently disable ADK patch 4b for every campaign that does not
  name it -- went unseen. Tested separately now.

Re-run the check by hand after changing a body; it is deliberately not in CI, because a
harness that runs the suite ~45 times costs more than it earns on every push.
"""

from __future__ import annotations

import json
import os
import sys
import types
from unittest import mock

import pytest

from tests.pipeline_component_harness import component_io
from wrangler.pipeline import components

RUN = "test-run"
BUCKET = "test-bucket"
PROJECT = "test-project"

PAIR = {
    "id": "sonnet",
    "model": "claude-sonnet-5",
    "system_prompt": "You are a helpful travel agent.",
}


def _pair(**overrides) -> str:
    return json.dumps({**PAIR, **overrides})


def _stage(stage: str, pair_id: str = "sonnet") -> str:
    return f"pipeline-runs/{RUN}/stages/{stage}/{pair_id}.json"


# ── Component 1: archive ──────────────────────────────────────────


class TestArchiveVerifiesTheTarball:
    def test_it_returns_the_uri_and_records_the_size(self, tmp_path):
        with component_io(tmp_path) as io:
            uri = components.archive_agent_code.python_func(
                project_id=PROJECT,
                bucket_name=BUCKET,
                run_id=RUN,
                manifest_json=json.dumps({"pairs": [{"id": "a"}, {"id": "b"}]}),
                metrics=io.metrics,
                summary=io.summary,
            )

        assert uri == f"gs://{BUCKET}/pipeline-runs/{RUN}/code.tar.gz"
        assert "tarball_size_kb" in io.metrics.logged
        assert "**Pairs**: 2" in io.summary.read()

    def test_a_missing_tarball_fails_loudly_and_names_the_path(self, tmp_path):
        """The one thing this component exists to catch.

        It runs before deploy, so a clear failure here costs seconds; the same missing
        tarball discovered inside the optimize container costs a stage.
        """
        with component_io(tmp_path) as io:
            io.gcs.blobs.clear()
            with pytest.raises(RuntimeError, match=r"code\.tar\.gz"):
                components.archive_agent_code.python_func(
                    project_id=PROJECT,
                    bucket_name=BUCKET,
                    run_id=RUN,
                    manifest_json=json.dumps({"pairs": []}),
                    metrics=io.metrics,
                    summary=io.summary,
                )


# ── Component 2: deploy ───────────────────────────────────────────


def _run_deploy(io, *, pair_json: str, health_gate: dict | None = None, secret_id: str = ""):
    return components.deploy_single_agent.python_func(
        project_id=PROJECT,
        location="us-central1",
        bucket_name=BUCKET,
        run_id=RUN,
        pair_json=pair_json,
        agent_module="examples/multi_model_agents/agents/sonnet_agent.py",
        secret_id=secret_id,
        cache_bust="v1",
        health_gate_json=json.dumps(health_gate) if health_gate is not None else "",
        engine_labels_json=json.dumps({"campaign": "09", "lifecycle": "ephemeral"}),
        **io.outputs,
    )


class TestDeployExtractsTheCodeTarballSafely:
    def test_extraction_is_filtered(self, tmp_path):
        """`filter="data"` rejects absolute paths, `..` escapes and special files.

        Without it a tarball can write anywhere in the container. Asserted behaviourally
        because the string `filter="data"` appearing in the file proves nothing about the
        call that actually runs.
        """
        with component_io(tmp_path) as io:
            _run_deploy(io, pair_json=_pair(engine_id="reuse-me"))

        assert io.extract_kwargs().get("filter") == "data"
        assert io.extract_kwargs().get("path") == "/app"


class TestDeployReusesAnExistingEngine:
    def test_it_does_not_deploy_when_the_pair_names_an_engine(self, tmp_path):
        """`engine_id` on a pair means "use this one" -- deploying anyway would both burn
        ~12 minutes and leave an orphan engine nothing reaps."""
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.core.deploy.deploy_agent_from_source") as deploy,
        ):
            out = json.loads(_run_deploy(io, pair_json=_pair(engine_id="4023875557346246656")))

        deploy.assert_not_called()
        assert out["engine_id"] == "4023875557346246656"
        assert out["source"] == "existing"

    def test_the_stage_artifact_lands_where_eval_looks_for_it(self, tmp_path):
        """`eval_single_agent` reads `stages/deploy/{pair_id}.json` by path. A typo here
        is only discoverable at runtime, one stage later."""
        with component_io(tmp_path) as io:
            _run_deploy(io, pair_json=_pair(engine_id="reuse-me"))

        assert _stage("deploy") in io.gcs.blobs
        assert json.loads(io.gcs.blobs[_stage("deploy")])["engine_id"] == "reuse-me"


class TestDeployLabelsEveryEngineItCreates:
    def test_the_ownership_label_survives_a_manifest_that_omits_it(self, tmp_path):
        """`wrangler engines prune` refuses to delete anything without `solution`, so an
        engine that loses it is unreapable -- the project reached 80 engines that way."""
        with (
            component_io(tmp_path) as io,
            mock.patch(
                "wrangler.core.deploy.deploy_agent_from_source", return_value="eng-1"
            ) as deploy,
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ),
        ):
            _run_deploy(io, pair_json=_pair())

        labels = deploy.call_args.kwargs["labels"]
        assert labels["solution"] == "promp-wrangler"
        assert labels["campaign"] == "09"
        assert labels["lifecycle"] == "ephemeral"


class TestDeployHealthGateWiring:
    def test_a_disabled_gate_does_no_probing(self, tmp_path):
        """~12 minutes and ~60 requests per pair. A manifest that turns the gate off must
        not pay it -- reading the config and ignoring `enabled` is the divergence this
        component was refactored to stop having."""
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.core.deploy.deploy_agent_from_source", return_value="eng-1"),
            mock.patch("wrangler.orchestration.stages.gate_engine_health") as gate,
        ):
            out = json.loads(_run_deploy(io, pair_json=_pair(), health_gate={"enabled": False}))

        gate.assert_not_called()
        assert out["health"]["skipped"] is True

    def test_deploy_passes_a_discard_fn_because_each_reroll_is_a_new_engine(self, tmp_path):
        """The mirror of redeploy's `discard_fn=None`. Deploy *must* discard: every reroll
        creates a genuinely new engine, and not discarding leaks one per reroll."""
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.core.deploy.deploy_agent_from_source", return_value="eng-1"),
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ) as gate,
        ):
            _run_deploy(io, pair_json=_pair())

        assert gate.call_args.kwargs["discard_fn"] is not None

    def test_the_post_reroll_engine_id_is_the_one_recorded(self, tmp_path):
        """A reroll replaces the engine. Recording the pre-reroll id would point every
        later stage, and the teardown, at an engine that was already discarded."""
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.core.deploy.deploy_agent_from_source", return_value="eng-bad"),
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-good", "passed": True, "rejected": ["eng-bad"]},
            ),
            mock.patch("wrangler.tools.engines.delete_engine") as delete,
        ):
            out = json.loads(_run_deploy(io, pair_json=_pair()))

        assert out["engine_id"] == "eng-good"
        delete.assert_called_once_with("eng-bad")

    def test_a_reject_the_gate_already_discarded_is_not_deleted_twice(self, tmp_path):
        """The gate discards its own draws. Re-deleting them is a guaranteed NotFound
        logged as "could not discard" -- a warning that reads like a leak and is its
        opposite."""
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.core.deploy.deploy_agent_from_source", return_value="eng-1"),
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": ["eng-1"]},
            ),
            mock.patch("wrangler.tools.engines.delete_engine") as delete,
        ):
            _run_deploy(io, pair_json=_pair())

        delete.assert_not_called()


def _fake_secretmanager(payload: str):
    """Stand in for `google.cloud.secretmanager`, which is only installed in the image."""
    client = mock.MagicMock()
    client.access_secret_version.return_value.payload.data = payload.encode()
    module = types.ModuleType("google.cloud.secretmanager")
    module.SecretManagerServiceClient = mock.MagicMock(return_value=client)
    return mock.patch.dict(sys.modules, {"google.cloud.secretmanager": module})


# ── Component 3: eval ─────────────────────────────────────────────


class _FakeEvalResult:
    def __init__(self, scores, per_case_n=64):
        self.scores = scores
        self.scores_std = dict.fromkeys(scores, 0.01)
        self.per_case = [{"case": i} for i in range(per_case_n)]
        self.num_runs = 2
        self.token_usage = {"input_tokens": 1000, "output_tokens": 200}


def _run_eval(io, *, phase: str, pair_json: str | None = None, canary_path: str = ""):
    return components.eval_single_agent.python_func(
        project_id=PROJECT,
        location="us-central1",
        bucket_name=BUCKET,
        run_id=RUN,
        pair_json=pair_json or _pair(),
        eval_data_path="data/eval.json",
        phase=phase,
        num_runs=2,
        score_repeats=2,
        judge_model="gemini-3.5-flash",
        redeploy_output="",
        cache_bust="v1",
        canary_path=canary_path,
        **io.outputs,
    )


@pytest.fixture
def eval_patches():
    """`load_eval_file` and `run_batch_eval_averaged` are the two cloud-touching calls."""
    with (
        mock.patch(
            "wrangler.core.converter.load_eval_file", return_value=[{"prompt": "p"}] * 64
        ) as load,
        mock.patch(
            "wrangler.eval.evaluator.run_batch_eval_averaged",
            return_value=_FakeEvalResult({"safety_v1": 0.9, "instruction_following_v1": 0.8}),
        ) as run,
    ):
        yield load, run


class TestEvalResolvesThePromptItIsScoring:
    def test_a_control_arm_falls_back_to_the_deployed_prompt(self, tmp_path, eval_patches):
        """A control arm runs `phase="after"` with **no optimize stage** -- that is the
        point of it. Downloading the optimize artifact unconditionally is what failed the
        second validation run, after deploy and eval_before had both succeeded. Absent
        means unchanged, which is the correct answer and not a guess.
        """
        seed = {
            _stage("deploy"): json.dumps(
                {"engine_id": "eng-1", "original_prompt": "the unchanged prompt"}
            )
        }
        with component_io(tmp_path, seed=seed) as io:
            _run_eval(io, phase="after")

        assert "the unchanged prompt" in io.agent_prompt.read()

    def test_an_optimized_prompt_is_used_when_the_optimize_stage_wrote_one(
        self, tmp_path, eval_patches
    ):
        seed = {
            _stage("deploy"): json.dumps({"engine_id": "eng-1", "original_prompt": "seed"}),
            _stage("optimize"): json.dumps({"optimized_prompt": "the evolved prompt"}),
        }
        with component_io(tmp_path, seed=seed) as io:
            _run_eval(io, phase="after")

        assert "the evolved prompt" in io.agent_prompt.read()

    def test_an_empty_optimized_prompt_falls_back_rather_than_evaluating_nothing(
        self, tmp_path, eval_patches
    ):
        """GEPA returning an empty string must not silently become the agent's prompt."""
        seed = {
            _stage("deploy"): json.dumps({"engine_id": "eng-1", "original_prompt": "seed prompt"}),
            _stage("optimize"): json.dumps({"optimized_prompt": ""}),
        }
        with component_io(tmp_path, seed=seed) as io:
            _run_eval(io, phase="after")

        assert "seed prompt" in io.agent_prompt.read()


class TestEvalRecordsCoverage:
    def test_scored_and_total_case_counts_are_both_written(self, tmp_path):
        """Coverage was only ever `len(per_case)`, which is why nobody noticed it swinging
        42 points between the two sides of one arm. A delta computed across a swing that
        size measures dropout, not the prompt."""
        seed = {_stage("deploy"): json.dumps({"engine_id": "eng-1", "original_prompt": "p"})}
        with (
            component_io(tmp_path, seed=seed) as io,
            mock.patch("wrangler.core.converter.load_eval_file", return_value=[{"p": 1}] * 64),
            mock.patch(
                "wrangler.eval.evaluator.run_batch_eval_averaged",
                return_value=_FakeEvalResult({"safety_v1": 0.9}, per_case_n=57),
            ),
        ):
            out = json.loads(_run_eval(io, phase="before"))

        assert out["cases_scored"] == 57
        assert out["cases_total"] == 64
        assert out["coverage"] == pytest.approx(57 / 64)

    @pytest.mark.parametrize(("phase", "other"), [("before", "after"), ("after", "before")])
    def test_the_phase_selects_the_stage_directory(self, tmp_path, eval_patches, phase, other):
        """`eval_before` and `eval_after` must not collide -- the report reads both, and an
        after-side written over the before-side destroys the delta.

        **Both phases, deliberately.** Checking only `before` passes against a body that
        hardcodes `stage_name = "eval_before"`, which is a real way to lose the after-side;
        a mutation run caught this test doing exactly that.
        """
        seed = {_stage("deploy"): json.dumps({"engine_id": "eng-1", "original_prompt": "p"})}
        with component_io(tmp_path, seed=seed) as io:
            _run_eval(io, phase=phase)

        assert _stage(f"eval_{phase}") in io.gcs.blobs
        assert _stage(f"eval_{other}") not in io.gcs.blobs


# ── Component 4: optimize (control-arm path only) ─────────────────


def _run_optimize(io, *, pair_json: str, secret_id: str = ""):
    return components.optimize_single_agent.python_func(
        project_id=PROJECT,
        location="us-central1",
        bucket_name=BUCKET,
        run_id=RUN,
        pair_json=pair_json,
        eval_data_path="data/eval.json",
        agent_module="examples/multi_model_agents/agents/sonnet_agent.py",
        judge_model="gemini-3.5-flash",
        secret_id=secret_id,
        max_metric_calls=600,
        cache_bust="v1",
        metrics=io.metrics,
        summary=io.summary,
    )


class TestOptimizeShortCircuitsAControlArm:
    """A control arm's whole job is to produce a delta from an **unchanged** prompt. If
    GEPA runs, the arm measures optimization instead of the noise floor, and the campaign
    loses its gate."""

    def test_gepa_is_never_invoked(self, tmp_path):
        with (
            component_io(tmp_path) as io,
            mock.patch("wrangler.optimize.optimizer.optimize") as gepa,
        ):
            _run_optimize(io, pair_json=_pair(skip_optimize=True))

        gepa.assert_not_called()

    def test_the_prompt_comes_back_byte_identical(self, tmp_path):
        """`PairAnalysis.is_control` detects the arm by `original == optimized`, so
        "equivalent" is not good enough."""
        prompt = "You are a helpful travel agent.\n\nBe concise.\n"
        with component_io(tmp_path) as io:
            returned = _run_optimize(io, pair_json=_pair(system_prompt=prompt, skip_optimize=True))

        assert returned == prompt
        written = json.loads(io.gcs.blobs[_stage("optimize")])
        assert written["optimized_prompt"] == prompt
        assert written["control_arm"] is True

    def test_it_still_writes_the_artifact_redeploy_reads(self, tmp_path):
        """Redeploy downloads `stages/optimize/{pair}.json` unconditionally. A control arm
        that skipped the write would 404 the next stage."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(skip_optimize=True))

        assert _stage("optimize") in io.gcs.blobs

    def test_costs_are_explicit_zeros_not_omitted(self, tmp_path):
        """A report summing spend across arms should not need to special-case the arm that
        spent nothing."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(skip_optimize=True))

        written = json.loads(io.gcs.blobs[_stage("optimize")])
        assert written["costs"] == {"input_usd": 0.0, "output_usd": 0.0}
        assert written["token_usage"]["is_estimate"] is True


@pytest.fixture
def gepa(tmp_path):
    """Stand in for the GEPA run itself.

    Only `optimize()` and `gepa_run_dir()` are faked -- everything between the secrets
    block and the stage artifact is the component's own code, which is the part worth
    running. The MCP servers do not start because `/app` does not exist on a dev machine,
    which is also what keeps this fast.
    """
    with (
        mock.patch("wrangler.optimize.optimizer.optimize", return_value="EVOLVED") as opt,
        mock.patch("wrangler.optimize.optimizer.gepa_run_dir", return_value=tmp_path / "gepa"),
    ):
        yield opt


class TestOptimizeDrivesGepaWithTheRightPrompt:
    def test_the_manifest_prompt_is_what_gepa_starts_from(self, tmp_path, gepa):
        """`initial_instruction` overrides whatever `*_opt/__init__.py` carries. Without
        it GEPA would evolve the agent module's own instruction while `eval_before`
        measured the manifest's -- the two sides of the campaign would not be the same
        prompt, and the delta would be meaningless.
        """
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(system_prompt="the manifest prompt"))

        assert gepa.call_args.kwargs["initial_instruction"] == "the manifest prompt"

    def test_the_optimized_prompt_reaches_the_artifact_redeploy_reads(self, tmp_path, gepa):
        with component_io(tmp_path) as io:
            returned = _run_optimize(io, pair_json=_pair())

        assert json.loads(returned)["optimized_prompt"] == "EVOLVED"
        assert json.loads(io.gcs.blobs[_stage("optimize")])["optimized_prompt"] == "EVOLVED"


class TestOptimizeForwardsThePerPairFactors:
    @pytest.mark.parametrize("flag", [True, False])
    def test_rationale_forwarding_follows_the_pair(self, tmp_path, gepa, flag):
        """ADK patch 4b, and campaign 09's only factor. It was wired into the pipeline and
        not the local path when introduced, so a manifest got a different experiment
        depending on how it was launched."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(forward_rationale=flag))

        assert gepa.call_args.kwargs["forward_rationale"] is flag

    def test_rationale_forwarding_defaults_on_when_the_pair_is_silent(self, tmp_path, gepa):
        """Tested separately from the parametrised case above, which always sets the key
        and so cannot see the default move.

        Almost no manifest sets this -- patch 4b is meant to be on. A default flipped to
        `False` would take the rationale away from every campaign that did not ask for it,
        starving the reflection step while every test still passed. A mutation run found
        this gap.
        """
        pair = json.loads(_pair())
        assert "forward_rationale" not in pair, "this test is only meaningful if the key is absent"

        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=json.dumps(pair))

        assert gepa.call_args.kwargs["forward_rationale"] is True

    def test_the_judge_model_is_passed_through(self, tmp_path, gepa):
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        assert gepa.call_args.kwargs["judge_model"] == "gemini-3.5-flash"

    def test_a_zero_budget_means_unbounded_not_zero_calls(self, tmp_path, gepa):
        """`max_metric_calls=0` passed straight through would ask GEPA for no evaluations
        at all, which returns the seed prompt and looks exactly like a converged run."""
        with component_io(tmp_path) as io:
            components.optimize_single_agent.python_func(
                project_id=PROJECT,
                location="us-central1",
                bucket_name=BUCKET,
                run_id=RUN,
                pair_json=_pair(),
                eval_data_path="data/eval.json",
                agent_module="examples/multi_model_agents/agents/sonnet_agent.py",
                judge_model="gemini-3.5-flash",
                secret_id="",
                max_metric_calls=0,
                cache_bust="v1",
                metrics=io.metrics,
                summary=io.summary,
            )

        assert gepa.call_args.kwargs["max_metric_calls"] is None

    def test_an_absent_sampler_config_falls_back_rather_than_pointing_at_nothing(
        self, tmp_path, gepa
    ):
        """`sampler_config.json` is the source of truth when present. When it is absent the
        optimizer builds fallback criteria -- passing a path to a file that does not exist
        would fail inside GEPA instead."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        assert gepa.call_args.kwargs["sampler_config_path"] is None


class TestOptimizeForwardsPerPairCampaignFactors:
    """These ride in `pair_json`, which the DAG forwards verbatim, so a factor that is
    parsed but never read looks identical to one that works until you read the logs."""

    def test_patience_reaches_the_optimizer(self, tmp_path, gepa):
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(patience=15))

        assert gepa.call_args.kwargs["patience"] == 15

    def test_continuous_val_score_reaches_the_optimizer(self, tmp_path, gepa):
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair(continuous_val_score=True))

        assert gepa.call_args.kwargs["continuous_val_score"] is True

    def test_continuous_val_score_defaults_off(self, tmp_path, gepa):
        """It changes what GEPA selects on, so a pair that says nothing must run the old
        scoring or every in-flight campaign silently re-baselines."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        assert gepa.call_args.kwargs["continuous_val_score"] is False

    def test_absent_patience_passes_none_rather_than_a_default(self, tmp_path, gepa):
        """Early stopping is opt-in. A pair that says nothing must run unstopped, or
        every in-flight campaign silently changes its budget."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        assert gepa.call_args.kwargs["patience"] is None


@pytest.fixture
def gepa_with_run_dir(tmp_path):
    """A GEPA run that left real files behind, so the shipping loop actually runs.

    Without this the loop is skipped (`run_dir_path.is_dir()` is false) and any test of it
    passes vacuously -- which the first version of these tests did.
    """
    run_dir = tmp_path / "gepa_run"
    (run_dir / "nested").mkdir(parents=True)
    (run_dir / "candidates.json").write_text('{"candidates": []}')
    (run_dir / "nested" / "state.pkl").write_text("pickle-bytes")
    with (
        mock.patch("wrangler.optimize.optimizer.optimize", return_value="EVOLVED"),
        mock.patch("wrangler.optimize.optimizer.gepa_run_dir", return_value=run_dir),
    ):
        yield run_dir


class TestOptimizeShipsGepasRunDir:
    def test_every_file_is_uploaded_under_the_pair(self, tmp_path, gepa_with_run_dir):
        """`candidates.json` holds the prompts without scores; the pickle has both. Losing
        them means a campaign whose result cannot be explained after the fact."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        shipped = [p for p in io.gcs.blobs if "gepa_run" in p]
        assert sorted(shipped) == [
            f"pipeline-runs/{RUN}/stages/optimize/gepa_run/sonnet/candidates.json",
            f"pipeline-runs/{RUN}/stages/optimize/gepa_run/sonnet/nested/state.pkl",
        ]

    def test_the_state_file_is_uploaded_before_anything_that_can_grow(self, tmp_path):
        """`gepa_state.bin` must go first, ahead of generated_best_outputs_valset/.

        Plain alphabetical order puts "generated..." before "gepa_state.bin", and that
        directory is the one that grows with the eval set -- so the file carrying every
        candidate's validation subscores was first in line to be dropped by the size cap.
        """
        run_dir = tmp_path / "gepa_run"
        (run_dir / "generated_best_outputs_valset").mkdir(parents=True)
        (run_dir / "generated_best_outputs_valset" / "0.json").write_text("{}")
        (run_dir / "candidates.json").write_text("{}")
        (run_dir / "gepa_state.bin").write_bytes(b"state")

        with (
            mock.patch("wrangler.optimize.optimizer.optimize", return_value="EVOLVED"),
            mock.patch("wrangler.optimize.optimizer.gepa_run_dir", return_value=run_dir),
            component_io(tmp_path) as io,
        ):
            _run_optimize(io, pair_json=_pair())

        shipped = [p.rsplit("/", 1)[-1] for p in io.gcs.blobs if "gepa_run" in p]
        assert shipped[:2] == ["gepa_state.bin", "candidates.json"]

    def test_the_state_file_survives_a_blown_size_budget(self, tmp_path):
        """The failure the ordering prevents, reproduced rather than argued.

        A sparse file reports its full size to `stat()` without occupying disk, so the
        100 MB cap can be exhausted for real in a test that costs nothing.
        """
        run_dir = tmp_path / "gepa_run"
        (run_dir / "generated_best_outputs_valset").mkdir(parents=True)
        huge = run_dir / "generated_best_outputs_valset" / "0.json"
        with open(huge, "wb") as f:
            f.truncate(101 * 1024 * 1024)
        (run_dir / "gepa_state.bin").write_bytes(b"state")

        with (
            mock.patch("wrangler.optimize.optimizer.optimize", return_value="EVOLVED"),
            mock.patch("wrangler.optimize.optimizer.gepa_run_dir", return_value=run_dir),
            component_io(tmp_path) as io,
        ):
            _run_optimize(io, pair_json=_pair())

        shipped = [p.rsplit("/", 1)[-1] for p in io.gcs.blobs if "gepa_run" in p]
        assert "gepa_state.bin" in shipped
        assert "0.json" not in shipped, "the oversized file should still be skipped"

    def test_a_failure_shipping_them_does_not_lose_the_stage(self, tmp_path, gepa_with_run_dir):
        """Nine hours of compute must not be thrown away because a diagnostics upload
        failed. The optimized prompt is the product; the run_dir is evidence.

        The upload is made to fail for real -- an earlier version of this test patched
        nothing that the component actually called, so it passed against a body with no
        error handling at all.
        """
        with component_io(tmp_path) as io:
            real_blob = io.gcs.bucket

            def exploding_bucket(name):
                bucket = real_blob(name)
                original_blob = bucket.blob

                def blob(path):
                    b = original_blob(path)
                    if "gepa_run" in path:
                        b.upload_from_filename = mock.Mock(
                            side_effect=RuntimeError("upload failed")
                        )
                    return b

                bucket.blob = blob
                return bucket

            io.gcs.bucket = exploding_bucket
            returned = _run_optimize(io, pair_json=_pair())

        assert json.loads(returned)["optimized_prompt"] == "EVOLVED"
        assert _stage("optimize") in io.gcs.blobs, "the stage artifact must still be written"


# ── Component 5: redeploy ─────────────────────────────────────────


def _run_redeploy(
    io, *, pair_json: str | None = None, health_gate: dict | None = None, secret_id: str = ""
):
    return components.redeploy_single_agent.python_func(
        project_id=PROJECT,
        location="us-central1",
        bucket_name=BUCKET,
        run_id=RUN,
        pair_json=pair_json or _pair(),
        agent_module="examples/multi_model_agents/agents/sonnet_agent.py",
        secret_id=secret_id,
        optimize_output="",
        cache_bust="v1",
        health_gate_json=json.dumps(health_gate) if health_gate is not None else "",
        engine_labels_json=json.dumps({"campaign": "09", "lifecycle": "ephemeral"}),
        **io.outputs,
    )


REDEPLOY_SEED = {
    _stage("deploy"): json.dumps(
        {"engine_id": "eng-1", "original_prompt": "seed prompt", "model": "claude-sonnet-5"}
    ),
    _stage("optimize"): json.dumps({"optimized_prompt": "evolved prompt"}),
}


class TestRedeployKeepsTheEngineReapable:
    def test_labels_are_sent_with_the_update(self, tmp_path):
        """`runtimes.update()` OVERWRITES labels rather than merging, so omitting them
        strips whatever deploy set. Every arm passes through redeploy, so until 2026-09-21
        no engine kept its `lifecycle: ephemeral` -- the evidence `prune` needs.
        """
        with (
            component_io(tmp_path, seed=REDEPLOY_SEED) as io,
            mock.patch("wrangler.core.deploy.update_agent_from_source") as update,
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ),
        ):
            _run_redeploy(io)

        labels = update.call_args.kwargs["labels"]
        assert labels["solution"] == "promp-wrangler"
        assert labels["lifecycle"] == "ephemeral"
        assert labels["campaign"] == "09"


class TestRedeployMustNotDeleteTheLiveEngine:
    def test_no_discard_fn_is_passed(self, tmp_path):
        """An in-place update returns the **same** engine id, so after one reroll that id
        is in the gate's `gate_created` set and a `discard_fn` would delete the engine the
        campaign is running on. Deploy can discard; redeploy cannot."""
        with (
            component_io(tmp_path, seed=REDEPLOY_SEED) as io,
            mock.patch("wrangler.core.deploy.update_agent_from_source"),
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ) as gate,
        ):
            _run_redeploy(io)

        assert gate.call_args.kwargs["discard_fn"] is None


class TestRedeployRecordsItsVerdictEvenWhenItFails:
    def test_the_artifact_is_written_before_the_gate_is_enforced(self, tmp_path):
        """Unlike deploy, redeploy enforces *after* the write. A hard gate failure should
        still leave its verdict on GCS -- otherwise the one run whose engine was too sick
        to use is also the one run with no record of why it stopped."""
        failing = {"engine_id": "eng-1", "passed": False, "rejected": [], "rate": 0.2}
        with (
            component_io(tmp_path, seed=REDEPLOY_SEED) as io,
            mock.patch("wrangler.core.deploy.update_agent_from_source"),
            mock.patch("wrangler.orchestration.stages.gate_engine_health", return_value=failing),
            mock.patch(
                "wrangler.orchestration.stages.enforce_health_gate",
                side_effect=RuntimeError("gate failed"),
            ),
            pytest.raises(RuntimeError, match="gate failed"),
        ):
            _run_redeploy(io, health_gate={"required": True})

        assert _stage("redeploy") in io.gcs.blobs
        assert json.loads(io.gcs.blobs[_stage("redeploy")])["health"]["passed"] is False


class TestRedeployResolvesTheModel:
    def test_the_manifest_model_wins_over_the_deploy_record(self, tmp_path):
        """Two campaign 07 arms pointing at one agent module both optimized whatever
        `config.py` pinned, so the frontier differed only by label."""
        seed = dict(REDEPLOY_SEED)
        seed[_stage("deploy")] = json.dumps(
            {"engine_id": "eng-1", "original_prompt": "seed", "model": "stale-model"}
        )
        with (
            component_io(tmp_path, seed=seed) as io,
            mock.patch("wrangler.core.deploy.update_agent_from_source") as update,
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ),
        ):
            _run_redeploy(io, pair_json=_pair(model="claude-sonnet-5"))

        assert update.call_args.kwargs["model"] == "claude-sonnet-5"

    def test_the_optimized_prompt_is_what_gets_deployed(self, tmp_path):
        with (
            component_io(tmp_path, seed=REDEPLOY_SEED) as io,
            mock.patch("wrangler.core.deploy.update_agent_from_source") as update,
            mock.patch(
                "wrangler.orchestration.stages.gate_engine_health",
                return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
            ),
        ):
            _run_redeploy(io)

        assert update.call_args.kwargs["instruction"] == "evolved prompt"


# ── Component 6: analysis ─────────────────────────────────────────


def _analysis_seed(*, with_optimize: bool, before_cov=1.0, after_cov=1.0) -> dict[str, str]:
    seed = {
        _stage("deploy"): json.dumps(
            {"engine_id": "eng-1", "original_prompt": "seed prompt", "health": {"passed": True}}
        ),
        _stage("eval_before"): json.dumps(
            {
                "scores": {"safety_v1": 0.80},
                "per_case": [],
                "scores_std": {},
                "num_runs": 2,
                "coverage": before_cov,
                "elapsed": 100,
                "token_usage": {"input_tokens": 10, "output_tokens": 5},
                "costs": {"input_usd": 0.1, "output_usd": 0.2},
            }
        ),
        _stage("eval_after"): json.dumps(
            {
                "scores": {"safety_v1": 0.88},
                "per_case": [],
                "scores_std": {},
                "coverage": after_cov,
                "elapsed": 100,
                "token_usage": {"input_tokens": 10, "output_tokens": 5},
                "costs": {"input_usd": 0.1, "output_usd": 0.2},
            }
        ),
    }
    if with_optimize:
        seed[_stage("optimize")] = json.dumps(
            {
                "optimized_prompt": "evolved prompt",
                "thresholds": {},
                "elapsed": 500,
                "token_usage": {"input_tokens": 100, "output_tokens": 50, "is_estimate": True},
                "costs": {"input_usd": 1.0, "output_usd": 2.0},
            }
        )
    return seed


def _run_analysis(io):
    return components.generate_analysis.python_func(
        project_id=PROJECT,
        location="us-central1",
        bucket_name=BUCKET,
        run_id=RUN,
        manifest_json=json.dumps({"name": "c09", "eval_data": "data/eval.json", "pairs": [PAIR]}),
        cache_bust="v1",
        metrics=io.metrics,
        summary=io.summary,
    )


class TestAnalysisSurvivesAnEvalOnlyRun:
    def test_a_missing_optimize_artifact_is_not_an_error(self, tmp_path):
        """A control arm has no optimize stage. Downloading it unconditionally is what
        killed the first control arm: the component exited 1 on a NotFound *after* deploy
        and both evals had already succeeded, throwing away the whole run's results."""
        with (
            component_io(tmp_path, seed=_analysis_seed(with_optimize=False)) as io,
            mock.patch("wrangler.reporting.reporter.generate_report"),
        ):
            out = json.loads(_run_analysis(io))

        assert out is not None
        assert io.metrics.logged["sonnet_delta"] == pytest.approx(0.08, abs=1e-6)


class TestAnalysisRecordsCoverageComparability:
    def test_a_coverage_gap_between_the_two_sides_is_computed(self, tmp_path):
        """A delta between a 47%-covered before and an 89%-covered after is mostly
        dropout. Averaging the two sides silently hides that."""
        seed = _analysis_seed(with_optimize=True, before_cov=0.47, after_cov=0.89)
        with (
            component_io(tmp_path, seed=seed) as io,
            mock.patch("wrangler.reporting.reporter.generate_report") as report,
        ):
            _run_analysis(io)

        results = report.call_args.args[0]
        assert results["sonnet"]["coverage_gap"] == pytest.approx(0.42, abs=1e-6)

    def test_an_absent_coverage_reading_yields_none_not_zero(self, tmp_path):
        """`None` means "not recorded"; `0.0` means "scored nothing". A report that
        conflates them claims total dropout on every pre-coverage run."""
        seed = _analysis_seed(with_optimize=True)
        seed[_stage("eval_after")] = json.dumps({"scores": {"safety_v1": 0.88}, "elapsed": 1})
        with (
            component_io(tmp_path, seed=seed) as io,
            mock.patch("wrangler.reporting.reporter.generate_report") as report,
        ):
            _run_analysis(io)

        assert report.call_args.args[0]["sonnet"]["coverage_gap"] is None


class TestAnalysisForwardsSpend:
    def test_usage_is_summed_across_stages_and_stays_marked_as_an_estimate(self, tmp_path):
        """`token_usage` lived only in the per-stage artifacts, so the measured-spend and
        $/quality-point columns rendered "n/a" on every real report. GEPA reports no
        metered count, so the estimate flag has to survive the sum -- a number that looks
        metered gets quoted as though it were."""
        with (
            component_io(tmp_path, seed=_analysis_seed(with_optimize=True)) as io,
            mock.patch("wrangler.reporting.reporter.generate_report") as report,
        ):
            _run_analysis(io)

        usage = report.call_args.args[0]["sonnet"]["token_usage"]
        assert usage["input_tokens"] == 120
        assert usage["output_tokens"] == 60
        assert usage["is_estimate"] is True

    def test_no_recorded_usage_stays_empty_rather_than_reporting_zero_spend(self, tmp_path):
        """`{}` renders as "n/a". `{"input_tokens": 0}` renders as a free campaign."""
        seed = _analysis_seed(with_optimize=False)
        for stage in ("eval_before", "eval_after"):
            data = json.loads(seed[_stage(stage)])
            data.pop("token_usage")
            seed[_stage(stage)] = json.dumps(data)
        with (
            component_io(tmp_path, seed=seed) as io,
            mock.patch("wrangler.reporting.reporter.generate_report") as report,
        ):
            _run_analysis(io)

        assert report.call_args.args[0]["sonnet"]["token_usage"] == {}


class TestAnalysisDoesNotLoseAnEntireRunToAReportingBug:
    def test_a_crashing_reporter_still_leaves_the_summary_and_the_error(self, tmp_path):
        """The results are already paid for -- hours of compute and a spent eval budget.
        A reporter that raised inside the container took the summary, the uploaded report
        and the per-pair totals down with it."""
        with (
            component_io(tmp_path, seed=_analysis_seed(with_optimize=True)) as io,
            mock.patch(
                "wrangler.reporting.reporter.generate_report",
                side_effect=RuntimeError("matplotlib exploded"),
            ),
        ):
            out = _run_analysis(io)

        assert out is not None
        assert any("analysis_error" in p for p in io.gcs.blobs), (
            "the failure should be recorded on GCS, not only in the container log"
        )
        assert any("summary" in p for p in io.gcs.blobs), (
            "the run summary must survive a reporting crash"
        )

    def test_a_crash_in_the_presentation_step_is_also_absorbed(self, tmp_path):
        """The *outer* guard, which is wider than the one around `generate_report`.

        The first attempt wrapped only the reporter, so a failure in the metrics/summary
        writing that follows still killed the component -- with the reports already
        uploaded and the measurement already paid for. Mutation-checked separately from the
        inner guard: narrowing one leaves the other covering it, so a single mutation
        proves nothing about which one is load-bearing.
        """

        class ExplodingMetrics:
            path = "/dev/null"

            def log_metric(self, *_args, **_kwargs):
                msg = "metrics sink unavailable"
                raise RuntimeError(msg)

        with (
            component_io(tmp_path, seed=_analysis_seed(with_optimize=True)) as io,
            mock.patch("wrangler.reporting.reporter.generate_report"),
        ):
            io.metrics = ExplodingMetrics()
            out = _run_analysis(io)

        assert out is not None, "the measurement must survive a presentation failure"
        assert any("analysis_error" in p for p in io.gcs.blobs)


# ── The Secret Manager block, duplicated across three components ──


def _load_secrets_via_deploy(io, secret_id):
    _run_deploy(io, pair_json=_pair(engine_id="reuse-me"), secret_id=secret_id)


def _load_secrets_via_optimize(io, secret_id):
    # skip_optimize returns right after the secrets block, so this exercises the copy
    # without starting GEPA.
    _run_optimize(io, pair_json=_pair(skip_optimize=True), secret_id=secret_id)


def _load_secrets_via_redeploy(io, secret_id):
    with (
        mock.patch("wrangler.core.deploy.update_agent_from_source"),
        mock.patch(
            "wrangler.orchestration.stages.gate_engine_health",
            return_value={"engine_id": "eng-1", "passed": True, "rejected": []},
        ),
    ):
        _run_redeploy(io, secret_id=secret_id)


#: The Secret Manager block is hand-duplicated in these three bodies -- KFP serialises each
#: in isolation, so it cannot be extracted, and `components.py`'s own docstring says to
#: "grep and update ALL copies". Parametrising is the drift guard: one copy losing the
#: re-pin is exactly the failure that would otherwise ship.
SECRET_LOADERS = {
    "deploy": (_load_secrets_via_deploy, {}),
    "optimize": (_load_secrets_via_optimize, {}),
    "redeploy": (_load_secrets_via_redeploy, REDEPLOY_SEED),
}


@pytest.mark.parametrize("component_name", sorted(SECRET_LOADERS))
class TestSecretsCannotBreakVertexRoutingInAnyComponent:
    """The payload is applied with `load_dotenv(override=True)`, so it wins over anything
    the component set before it. Three keys have to be re-pinned *after* that call, in
    every copy of the block."""

    def test_an_api_key_in_the_payload_is_dropped(self, tmp_path, component_name):
        """`GOOGLE_API_KEY` overrides Vertex ADC, and the Evaluation Service rejects API
        keys outright. Leaving it set fails the stage with a 401."""
        loader, seed = SECRET_LOADERS[component_name]
        payload = "GOOGLE_API_KEY=leaked-key\nGEMINI_API_KEY=also-leaked\n"
        with component_io(tmp_path, seed=seed) as io, _fake_secretmanager(payload):
            loader(io, "wrangler-env")

            assert "GOOGLE_API_KEY" not in os.environ
            assert "GEMINI_API_KEY" not in os.environ
            assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "1"

    def test_a_regional_location_in_the_payload_is_re_pinned_to_global(
        self, tmp_path, component_name
    ):
        """The documented trap. Claude and Gemini 3.x are not servable from a region, and
        GEPA's writer reads this env var directly through `Claude._anthropic_client` -- a
        regional value fails the whole optimize stage. Each component sets `global` before
        loading secrets, so only the re-pin *after* `load_dotenv(override=True)` saves it.
        """
        loader, seed = SECRET_LOADERS[component_name]
        with (
            component_io(tmp_path, seed=seed) as io,
            _fake_secretmanager("GOOGLE_CLOUD_LOCATION=us-central1\n"),
        ):
            loader(io, "wrangler-env")

            assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"

    def test_no_secret_id_means_no_secret_manager_call(self, tmp_path, component_name):
        """An empty `secret_id` is the no-secrets configuration. Reaching for Secret
        Manager anyway would fail a run that has nothing to load."""
        loader, seed = SECRET_LOADERS[component_name]
        with component_io(tmp_path, seed=seed) as io, _fake_secretmanager(""):
            module = sys.modules["google.cloud.secretmanager"]
            loader(io, "")

        module.SecretManagerServiceClient.assert_not_called()


# ── The sampler config, which decides what GEPA optimises against ──


SAMPLER_CONFIG = {
    "eval_config": {
        "criteria": {
            # ADK registers the PLURAL name; every report reads the SINGULAR one.
            "hallucinations_v1": {"threshold": 0.7},
            "safety_v1": {"threshold": 0.9},
            "rubric_based_final_response_quality_v1": {"threshold": 0.85},
            # A bare number is also legal, and means the threshold directly.
            "rubric_based_tool_use_quality_v1": 0.8,
        }
    }
}


@pytest.fixture
def sampler_config(tmp_path):
    """Put a real `sampler_config.json` where the component will find it.

    `resolve_agent_paths` is patched rather than the filesystem, because the component
    resolves against `/app/...`, which does not exist outside the container. The resolver
    itself is unit-tested in `test_pipeline_steps.py`.
    """
    agent_dir = tmp_path / "sonnet_opt"
    agent_dir.mkdir()
    cfg = agent_dir / "sampler_config.json"
    cfg.write_text(json.dumps(SAMPLER_CONFIG))
    with mock.patch(
        "wrangler.pipeline._steps.resolve_agent_paths",
        return_value={
            "agent_path": agent_dir,
            "project_root": tmp_path,
            "sampler_config": cfg,
        },
    ):
        yield cfg


class TestOptimizeRecordsTheThresholdsItWasJudgedAgainst:
    """`sampler_config.json` is the single source of truth for GEPA's criteria -- manifest
    thresholds do NOT override it. The stage artifact carries them so a report can mark
    pass/fail against what was actually optimised, rather than what a manifest claimed."""

    def test_the_plural_adk_name_is_mapped_to_the_singular_report_name(
        self, tmp_path, gepa, sampler_config
    ):
        """ADK registers `hallucinations_v1`; every report and eval reads
        `hallucination_v1`. Recording the raw key attaches the threshold to a metric no
        report looks up, and the column silently renders unmarked."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        thresholds = json.loads(io.gcs.blobs[_stage("optimize")])["thresholds"]
        assert thresholds["hallucination_v1"] == 0.7
        assert "hallucinations_v1" not in thresholds

    def test_names_that_need_no_mapping_pass_through(self, tmp_path, gepa, sampler_config):
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        thresholds = json.loads(io.gcs.blobs[_stage("optimize")])["thresholds"]
        assert thresholds["safety_v1"] == 0.9
        assert thresholds["final_response_quality_v1"] == 0.85

    def test_a_bare_number_is_read_as_the_threshold(self, tmp_path, gepa, sampler_config):
        """Both shapes appear in the checked-in configs, so both have to work."""
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        thresholds = json.loads(io.gcs.blobs[_stage("optimize")])["thresholds"]
        assert thresholds["tool_use_quality_v1"] == 0.8

    def test_the_config_is_handed_to_gepa_when_it_exists(self, tmp_path, gepa, sampler_config):
        with component_io(tmp_path) as io:
            _run_optimize(io, pair_json=_pair())

        assert gepa.call_args.kwargs["sampler_config_path"] == str(sampler_config)

    def test_a_baseline_already_above_every_threshold_is_flagged_not_skipped(
        self, tmp_path, gepa, sampler_config, caplog
    ):
        """GEPA is still run -- the pre-flight is a warning, not a gate. An arm that
        silently skipped optimization would look like a converged result rather than a
        misconfigured threshold, which is the harder failure to notice."""
        seed = {
            _stage("eval_before"): json.dumps(
                {
                    "scores": {
                        "hallucination_v1": 0.99,
                        "safety_v1": 0.99,
                        "final_response_quality_v1": 0.99,
                        "tool_use_quality_v1": 0.99,
                    }
                }
            )
        }
        with component_io(tmp_path, seed=seed) as io, caplog.at_level("WARNING"):
            _run_optimize(io, pair_json=_pair())

        gepa.assert_called_once()
        assert "PRE-FLIGHT" in caplog.text
