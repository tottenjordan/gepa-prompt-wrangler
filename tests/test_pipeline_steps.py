"""Logic lifted out of KFP component bodies, so it can be tested at all.

`wrangler/pipeline/components.py` was **642 statements at 2% coverage** — the code that runs
a campaign unattended for 10-24 hours was the least tested in the repo, and its two longest
functions ran to 444 and 351 lines. Every defect found in that path so far (silent failures
#12 and #13, the `skip_optimize` gap, the label stripping) surfaced hours or weeks after the
fact because there was no way to exercise it.

**Why extraction is allowed here, when CLAUDE.md warns about helpers.** KFP serialises each
`@dsl.component` body in isolation, so a component cannot call a module-level helper defined
*in components.py*. The broader reading — that a component cannot import anything — is false
and was checked on 2026-09-01: every component extracts the tarball, calls
`sys.path.insert(0, "/app")`, and imports from `wrangler` freely. It already does so 16
times.

Moving logic into `wrangler/pipeline/_steps.py` therefore makes it testable **and** shrinks
what KFP serialises, which reduces the surface that silent-failures #13 acts on.
"""

from __future__ import annotations

import json

import pytest

from wrangler.pipeline._steps import build_engine_labels, deploy_stage_payload


class TestBuildEngineLabels:
    """The function whose absence let redeploy strip a campaign's reaping labels."""

    def test_the_ownership_label_is_always_present(self):
        assert build_engine_labels("")["solution"] == "promp-wrangler"

    def test_manifest_labels_are_merged_over_it(self):
        got = build_engine_labels('{"lifecycle": "ephemeral", "campaign": "09"}')
        assert got == {
            "solution": "promp-wrangler",
            "lifecycle": "ephemeral",
            "campaign": "09",
        }

    def test_the_ownership_label_cannot_be_overwritten(self):
        """An engine that loses `solution` becomes unreapable — prune refuses to touch it.

        Merging the caller's labels *over* the default would let a manifest typo orphan an
        engine permanently, and nothing would notice until an inventory weeks later.
        """
        got = build_engine_labels('{"solution": "something-else"}')
        assert got["solution"] == "promp-wrangler"

    @pytest.mark.parametrize("bad", ["", "   ", "not json", "[]", "null"])
    def test_malformed_input_degrades_to_the_default(self, bad):
        """A bad label string must not kill a deploy nine hours into a campaign."""
        assert build_engine_labels(bad) == {"solution": "promp-wrangler"}

    def test_non_string_values_are_coerced(self):
        """GCP label values are strings; a YAML `campaign: 9` arrives as an int."""
        assert build_engine_labels('{"campaign": 9}')["campaign"] == "9"


class TestDeployStagePayload:
    def test_it_carries_the_fields_the_report_reads(self):
        payload = deploy_stage_payload(
            pair_id="arm",
            engine_id="123",
            model="m",
            original_prompt="p",
            source="deployed",
            elapsed=12.5,
            health={"passed": True, "rate": 1.0},
        )
        for key in ("pair_id", "engine_id", "model", "original_prompt", "source", "elapsed"):
            assert key in payload, f"{key} missing — the analyzer reads it"

    def test_health_is_preserved_verbatim(self):
        """`health.passed: false` with the eval running anyway is a finding, not a footnote."""
        health = {"passed": False, "rate": 0.55, "rejected": ["9"]}
        assert (
            deploy_stage_payload(
                pair_id="a",
                engine_id="1",
                model="m",
                original_prompt="p",
                source="deployed",
                elapsed=1.0,
                health=health,
            )["health"]
            == health
        )

    def test_it_serialises(self):
        """It is written to GCS as JSON; a non-serialisable value fails the stage."""
        payload = deploy_stage_payload(
            pair_id="a",
            engine_id="1",
            model="m",
            original_prompt="p",
            source="reused",
            elapsed=0.0,
            health=None,
        )
        assert json.loads(json.dumps(payload, default=str))["source"] == "reused"


class TestTheComponentStillWorks:
    """Extraction must not break what KFP serialises."""

    def test_the_deploy_component_parses(self):
        import ast
        import inspect

        from wrangler.pipeline import components

        ast.parse(inspect.getsource(components.deploy_single_agent.python_func))

    def test_it_imports_the_extracted_helpers(self):
        import inspect

        from wrangler.pipeline import components

        src = inspect.getsource(components.deploy_single_agent.python_func)
        assert "_steps import" in src or "_steps." in src, (
            "the component no longer imports from _steps, so the extracted logic is dead "
            "code and the component has its own copy again"
        )

    def test_the_dag_still_compiles(self):
        from wrangler.pipeline import dag  # noqa: F401


class TestRedeployInputs:
    """Resolving what to redeploy, from the two upstream stage artifacts."""

    def test_it_reads_the_prompts_from_the_right_stages(self):
        from wrangler.pipeline._steps import redeploy_inputs

        got = redeploy_inputs(
            deploy_data={"engine_id": "e1", "original_prompt": "seed", "model": "m1"},
            optimize_data={"optimized_prompt": "evolved"},
            pair_model="",
        )
        assert got["engine_id"] == "e1"
        assert got["original_prompt"] == "seed"
        assert got["optimized_prompt"] == "evolved"

    def test_the_pair_model_wins_over_the_deploy_record(self):
        """Two c07 arms sharing an agent module optimized the module's pinned default."""
        from wrangler.pipeline._steps import redeploy_inputs

        got = redeploy_inputs(
            deploy_data={"engine_id": "e", "original_prompt": "s", "model": "from-deploy"},
            optimize_data={"optimized_prompt": "o"},
            pair_model="from-manifest",
        )
        assert got["model"] == "from-manifest"

    def test_it_falls_back_to_the_deploy_model_when_the_pair_is_silent(self):
        from wrangler.pipeline._steps import redeploy_inputs

        got = redeploy_inputs(
            deploy_data={"engine_id": "e", "original_prompt": "s", "model": "from-deploy"},
            optimize_data={"optimized_prompt": "o"},
            pair_model="",
        )
        assert got["model"] == "from-deploy"

    def test_an_unchanged_prompt_is_flagged(self):
        """A control arm redeploys the same prompt; the report says so rather than implying work."""
        from wrangler.pipeline._steps import redeploy_inputs

        same = redeploy_inputs(
            deploy_data={"engine_id": "e", "original_prompt": "identical", "model": "m"},
            optimize_data={"optimized_prompt": "identical"},
            pair_model="",
        )
        assert same["prompt_changed"] is False

        changed = redeploy_inputs(
            deploy_data={"engine_id": "e", "original_prompt": "a", "model": "m"},
            optimize_data={"optimized_prompt": "b"},
            pair_model="",
        )
        assert changed["prompt_changed"] is True


class TestRedeployStagePayload:
    def test_it_records_the_health_verdict(self):
        from wrangler.pipeline._steps import redeploy_stage_payload

        health = {"engine_id": "e", "passed": False, "rate": 0.5}
        got = redeploy_stage_payload(pair_id="a", engine_id="e", elapsed=3.0, health=health)
        assert got["health"] == health

    def test_updated_at_is_iso_utc(self):
        """Redeploy is the only stage that timestamps itself; reports parse it."""
        from wrangler.pipeline._steps import redeploy_stage_payload

        got = redeploy_stage_payload(pair_id="a", engine_id="e", elapsed=0.0, health={})
        from datetime import datetime

        parsed = datetime.fromisoformat(got["updated_at"])
        assert parsed.tzinfo is not None, "timestamp is naive; it must carry UTC"


class TestRedeployUsesTheSharedLabelMerge:
    """The duplicate merge is what let deploy and redeploy disagree in the first place."""

    def test_the_component_calls_build_engine_labels(self):
        import inspect

        from wrangler.pipeline import components

        src = inspect.getsource(components.redeploy_single_agent.python_func)
        assert "build_engine_labels" in src, (
            "redeploy has its own copy of the label merge again. Deploy and redeploy "
            "disagreeing on labels is exactly how campaign 09's engines lost theirs."
        )

    def test_the_redeploy_component_parses(self):
        import ast
        import inspect

        from wrangler.pipeline import components

        ast.parse(inspect.getsource(components.redeploy_single_agent.python_func))
