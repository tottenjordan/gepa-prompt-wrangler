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
