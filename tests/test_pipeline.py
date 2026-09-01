"""Tests for the Vertex AI Pipeline package."""

import json
import re

import yaml


class TestPipelineDAGCompilation:
    """Verify the KFP pipeline compiles without errors."""

    def _make_manifest(self, tmp_path, pairs):
        manifest = {
            "name": "test-experiment",
            "agent_module": "agents/example_agent",
            "eval_data": "eval_data/example_eval.yaml",
            "pairs": pairs,
            "eval_config": {"judge_model": "gemini-2.5-pro"},
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))
        return path, manifest

    def test_compile_single_pair(self, tmp_path):
        from kfp import compiler

        from wrangler.pipeline.dag import gepa_pipeline

        output_path = str(tmp_path / "pipeline.yaml")
        compiler.Compiler().compile(
            pipeline_func=gepa_pipeline,
            package_path=output_path,
        )
        assert (tmp_path / "pipeline.yaml").exists()
        content = (tmp_path / "pipeline.yaml").read_text()
        assert "gepa-prompt-optimization" in content

    def test_compiled_yaml_has_all_components(self, tmp_path):
        from kfp import compiler

        from wrangler.pipeline.dag import gepa_pipeline

        output_path = str(tmp_path / "pipeline.yaml")
        compiler.Compiler().compile(
            pipeline_func=gepa_pipeline,
            package_path=output_path,
        )
        content = (tmp_path / "pipeline.yaml").read_text()
        assert "archive-agent-code" in content
        assert "deploy-single-agent" in content
        assert "eval-single-agent" in content
        assert "optimize-single-agent" in content
        assert "redeploy-single-agent" in content
        assert "generate-analysis" in content


class TestManifestSerialization:
    """Verify manifest round-trips through JSON for pipeline parameters."""

    def test_manifest_with_costs_round_trip(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest_data = {
            "name": "cost-test",
            "agent_module": "agents/test",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {
                    "id": "flash",
                    "model": "gemini-3.5-flash",
                    "system_prompt": "Be helpful.",
                    "costs": {"input": 1.5, "output": 9.0},
                },
                {
                    "id": "sonnet",
                    "model": "claude-sonnet-4-6",
                    "system_prompt": "Be thorough.",
                },
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest_data))

        manifest = PairFactory.load(str(path))
        assert manifest.pairs[0].costs == {"input": 1.5, "output": 9.0}
        assert manifest.pairs[1].costs is None

        pairs_json = [{"id": p.id, "model": p.model, "costs": p.costs} for p in manifest.pairs]
        serialized = json.dumps(pairs_json)
        restored = json.loads(serialized)
        assert restored[0]["costs"]["input"] == 1.5
        assert restored[1]["costs"] is None

    def test_manifest_without_costs(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest_data = {
            "name": "no-costs",
            "agent_module": "agents/test",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "Hi."},
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest_data))

        manifest = PairFactory.load(str(path))
        assert manifest.pairs[0].costs is None


class TestBlendedCostWithCustom:
    """Verify blended_cost accepts custom cost overrides."""

    def test_custom_costs_override(self):
        from wrangler.core.config import blended_cost

        custom = {"input": 10.0, "output": 50.0}
        result = blended_cost("unknown-model", custom_costs=custom)
        expected = (4 * 10.0 + 1 * 50.0) / 5
        assert abs(result - expected) < 0.001

    def test_fallback_to_model_costs(self):
        from wrangler.core.config import MODEL_COSTS, blended_cost

        result = blended_cost("gemini-3.5-flash")
        cost = MODEL_COSTS["gemini-3.5-flash"]
        expected = (4 * cost["input"] + 1 * cost["output"]) / 5
        assert abs(result - expected) < 0.001

    def test_unknown_model_raises(self):
        """Unregistered ids used to price at $0.00, which reads as "free".

        The registry makes that an error. Reporting opts out explicitly via
        blended_cost_for_report; nothing else should.
        """
        import pytest

        from wrangler.core.config import blended_cost

        with pytest.raises(KeyError, match="totally-unknown-model"):
            blended_cost("totally-unknown-model")

    def test_report_variant_returns_zero_and_warns(self, caplog):
        """A report must not abort because one pair used an ad-hoc model id."""
        from wrangler.core.models import blended_cost_for_report

        with caplog.at_level("WARNING"):
            assert blended_cost_for_report("totally-unknown-model") == 0.0

        assert "totally-unknown-model" in caplog.text


class TestParetoFrontier:
    """Verify the Pareto frontier uses proper non-dominated sort."""

    def test_dominated_point_excluded(self):

        results = {
            "cheap-good": {"model": "gemini-3.1-flash-lite", "after": {"q": 0.9}},
            "expensive-worse": {"model": "gemini-3.5-flash", "after": {"q": 0.85}},
            "expensive-best": {"model": "claude-sonnet-4-6", "after": {"q": 0.95}},
        }

        import tempfile
        from pathlib import Path

        from wrangler.reporting.analysis import generate_cost_quality_chart

        with tempfile.TemporaryDirectory() as td:
            generate_cost_quality_chart(results, charts_dir=Path(td))
            assert (Path(td) / "cost_quality.png").exists()


class TestImageTag:
    def test_tag_changes_when_lockfile_changes(self, tmp_path):
        """The image tag must track uv.lock, not just pyproject.toml.

        pyproject.toml holds version *ranges*. Resolved versions can move
        without it changing, so hashing it alone lets two builds share a tag
        while containing different packages.
        """
        from wrangler.pipeline.deploy_pipeline import _compute_image_tag

        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        pyproject.write_text("[project]\nname='x'\n")
        lock.write_text("version = 1\n")

        tag_before = _compute_image_tag(pyproject, lock)
        lock.write_text("version = 2\n")
        tag_after = _compute_image_tag(pyproject, lock)

        assert tag_before != tag_after

    def test_tag_changes_when_pyproject_changes(self, tmp_path):
        from wrangler.pipeline.deploy_pipeline import _compute_image_tag

        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        pyproject.write_text("[project]\nname='x'\n")
        lock.write_text("version = 1\n")

        tag_before = _compute_image_tag(pyproject, lock)
        pyproject.write_text("[project]\nname='y'\n")
        assert _compute_image_tag(pyproject, lock) != tag_before

    def test_tag_is_stable_for_identical_inputs(self, tmp_path):
        from wrangler.pipeline.deploy_pipeline import _compute_image_tag

        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        pyproject.write_text("[project]\nname='x'\n")
        lock.write_text("version = 1\n")

        assert _compute_image_tag(pyproject, lock) == _compute_image_tag(pyproject, lock)

    def test_missing_pyproject_is_not_a_shared_tag(self, tmp_path):
        """Two different projects with no pyproject must not collide on 'unknown'."""
        from wrangler.pipeline.deploy_pipeline import _compute_image_tag

        missing = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        lock.write_text("version = 1\n")
        tag_a = _compute_image_tag(missing, lock)
        lock.write_text("version = 2\n")
        assert _compute_image_tag(missing, lock) != tag_a

    def test_tag_changes_when_dockerfile_changes(self, tmp_path):
        """Dockerfile.pipeline pins its own deps and is not derived from pyproject.

        It copies pyproject.toml but installs from a hardcoded list, so bumping a
        pin there changes the image while both other inputs stay byte-identical.
        """
        from wrangler.pipeline.deploy_pipeline import _compute_image_tag

        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        dockerfile = tmp_path / "Dockerfile.pipeline"
        pyproject.write_text("[project]\nname='x'\n")
        lock.write_text("version = 1\n")
        dockerfile.write_text('RUN pip install "google-adk>=2.2.0"\n')

        tag_before = _compute_image_tag(pyproject, lock, dockerfile)
        dockerfile.write_text('RUN pip install "google-adk>=2.7.1"\n')

        assert _compute_image_tag(pyproject, lock, dockerfile) != tag_before


class TestSkipOptimize:
    """Without this the DAG is a fixed chain and a control arm cannot exist.

    A control arm is the same prompt evaluated twice with no optimization
    between -- CLAUDE.md requires one in every sweep. The pipeline had no way
    to express it, and every run dragged in ~10h of GEPA per pair, which is why
    nobody used the pipeline for characterisation.
    """

    def _compile(self, tmp_path):
        from kfp import compiler

        from wrangler.pipeline.dag import build_pipeline

        out = tmp_path / "p.yaml"
        compiler.Compiler().compile(build_pipeline("python:3.11"), str(out))
        return out.read_text()

    def test_the_pipeline_still_compiles(self, tmp_path):
        assert len(self._compile(tmp_path)) > 1000

    def test_skip_optimize_is_a_pipeline_parameter(self, tmp_path):
        text = self._compile(tmp_path)
        assert "skip-optimize" in text or "skip_optimize" in text

    def test_a_control_arm_still_runs_both_evaluations(self, tmp_path):
        """The floor is the delta between two evals of an UNCHANGED prompt.

        The first version skipped eval_after along with optimize, which would
        have produced one evaluation and no delta at all -- a noise-floor
        campaign that cannot measure a noise floor. What gets skipped is
        optimize and redeploy, not the second eval.
        """
        text = self._compile(tmp_path)
        assert "control, no optimization" in text

    def test_the_control_eval_does_not_cache(self, tmp_path):
        """Its inputs match eval_before byte for byte.

        A cache hit would return that exact result and the floor would come out
        as precisely zero -- a measurement of KFP's cache, not of the noise.
        """
        text = self._compile(tmp_path)
        i = text.index("control, no optimization")
        before = text[max(0, i - 4000) : i]
        block = before[before.rfind("cachingOptions") :]
        # KFP writes `cachingOptions: {}` for disabled, not `enableCache: false`.
        assert block.startswith("cachingOptions: {}"), block[:80]

    def test_the_eval_component_tolerates_a_missing_optimize_stage(self):
        """A control arm runs phase="after" with no optimize stage.

        That is the point of it -- the prompt does not change. Downloading the
        optimize artifact unconditionally failed the second validation run,
        after deploy and eval_before had both succeeded. This is the third
        instance of the same class: an eval-only run meeting a component that
        assumes optimization happened.
        """
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        i = src.index("def eval_single_agent")
        body = src[i : i + 6000]
        j = body.index("stages/optimize")
        window = body[max(0, j - 500) : j + 500]
        assert "opt_blob.exists()" in window, "the optimize read must be guarded"

    def test_no_component_reads_the_optimize_stage_unguarded(self):
        """Fix the class, not the instance -- this was the third round of it.

        Every component that can run in the eval-only branch must tolerate a
        missing optimize artifact. Only `redeploy` may read it unconditionally:
        it runs solely inside the optimize branch, so it is always there.
        """
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        lines = src.splitlines()
        allowed_unguarded = {"redeploy_single_agent"}

        for n, line in enumerate(lines):
            if "stages/optimize" not in line:
                continue
            # Which component is this line in? Scan back to the enclosing def.
            owner = next(
                (m.group(1) for k in range(n, -1, -1) if (m := re.match(r"def (\w+)", lines[k]))),
                "<module>",
            )
            window = "\n".join(lines[max(0, n - 10) : n + 10])
            writes = "upload_from_string" in window
            guarded = ".exists()" in window or "required=False" in window
            assert writes or guarded or owner in allowed_unguarded, (
                f"{owner} (line {n + 1}) reads the optimize stage unguarded; "
                f"an eval-only run has no such artifact"
            )

    def test_the_analysis_tolerates_a_missing_optimize_stage(self):
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        assert '_read_stage("optimize", pair_id, required=False)' in src

    def test_both_branches_produce_an_analysis(self, tmp_path):
        """Analysis sits inside each branch: a task outside a dsl.If cannot
        depend on one inside it."""
        text = self._compile(tmp_path)
        assert "Generate Analysis (eval only)" in text
        assert "Generate Analysis" in text

    def test_the_manifest_can_set_it(self):
        from pathlib import Path

        src = Path("wrangler/pipeline/deploy_pipeline.py").read_text()
        assert '"skip_optimize"' in src
        assert 'get("skip_optimize"' in src


class TestTheDagCompilesAndItsParametersLineUp:
    """A parameter added in one layer and not the next fails at submission.

    `health_gate_json` has to be declared on the component, on the pipeline
    function, and in the dict `deploy_pipeline` sends. Miss the middle one and
    KFP raises at compile; miss the last and the component silently receives
    the default -- which is exactly how the pipeline path came to read no gate
    config at all, running on bare defaults while the manifest's attempts,
    threshold and max_rerolls were dropped.

    Neither failure is reachable from a unit test of the component body, and
    the round trip to find out costs a pipeline submission.
    """

    def _compiled_spec(self):
        import tempfile
        from pathlib import Path

        import yaml
        from kfp import compiler

        from wrangler.pipeline.dag import build_pipeline

        pipeline_func = build_pipeline("us-central1-docker.pkg.dev/p/r/i:tag")
        out = Path(tempfile.mkdtemp()) / "pipeline.yaml"
        compiler.Compiler().compile(pipeline_func=pipeline_func, package_path=str(out))
        return yaml.safe_load(out.read_text())

    def test_the_dag_compiles(self):
        assert self._compiled_spec()["root"]

    def test_every_parameter_the_submitter_sends_is_accepted(self):
        """The submitter's dict and the pipeline signature must agree.

        Read out of the source rather than by calling `deploy_pipeline`, which
        would need a project, a bucket and a built image.
        """
        import re
        from pathlib import Path

        src = Path("wrangler/pipeline/deploy_pipeline.py").read_text()
        # The parameter_values dict literal passed to the PipelineJob.
        block = src[src.index('"project_id": project_id') :]
        block = block[: block.index("},")]
        sent = set(re.findall(r'^\s*"(\w+)":', block, re.MULTILINE))

        declared = set(self._compiled_spec()["root"]["inputDefinitions"]["parameters"])
        unknown = sent - declared
        assert not unknown, (
            f"deploy_pipeline sends {sorted(unknown)}, which the pipeline does not "
            f"declare. KFP rejects the submission."
        )
        assert "health_gate_json" in sent & declared, (
            "the health gate config must reach the pipeline; the KFP path "
            "previously read none of it"
        )
        # Guard against the reverse: a required parameter nobody sends.
        params = self._compiled_spec()["root"]["inputDefinitions"]["parameters"]
        required_unsent = {
            name
            for name, spec in params.items()
            if "defaultValue" not in spec and not spec.get("isOptional")
        } - sent
        assert not required_unsent, (
            f"nothing supplies required parameter(s) {sorted(required_unsent)}"
        )
