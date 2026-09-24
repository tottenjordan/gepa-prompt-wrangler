"""`wrangler preflight` says whether a campaign can detect what it is hunting.

Campaign 09 pre-registered `safety_v1`, spent roughly forty hours and was filed
UNRESOLVED. Its minimum detectable effect was later measured as **larger than
the effect it was looking for**: nothing in the harness said so, before or
after. Preflight is the last thing run before a campaign launches, so that is
where the sentence belongs.

The failures these tests prevent, concretely:

- **the warning becoming a blocker.** Every campaign this repo has run would
  trip it. A check that fails the build on day one gets switched off rather
  than heeded, so `ok` stays True whatever the arithmetic says, and
  `run_preflight` keeps exiting 0.
- **the dependency checks regressing to make room for it.** Those two are
  load-bearing -- campaign 07 died twice in a GEAP build on a set that could
  not resolve -- and trading resolution coverage for a statistics warning would
  be a bad deal. They must report exactly as before, manifest or no manifest.
- **an MDE appearing for a design nobody described.** With no manifest there is
  no `num_runs` and no case count, and guessing them would put a fabricated
  number in front of the one reader who would believe it.
- **the numbers not reaching the reader.** `render` prints detail only for
  failures; an advisory that is silent because it passed is not a warning.
- **the provenance going missing.** The variance is campaign 09's, measured
  once. An MDE quoted without saying so repeats the mistake CLAUDE.md records:
  a floor is not a property of a metric.

The resolver is injected throughout so these stay hermetic, matching
`tests/test_preflight.py`.
"""

from __future__ import annotations

import yaml
from click.testing import CliRunner

from wrangler.cli import main
from wrangler.tools.preflight import (
    MDE_CHECK_NAME,
    design_sensitivity,
    manifest_design,
    render,
    run_preflight,
)

#: Campaign 09's real manifest: 64 eval cases at `num_runs: 2` -- the design
#: whose MDE on `safety_v1` (0.1031) exceeds the +0.0952 effect it went looking
#: for. The one campaign whose underpowering is documented.
C09_MANIFEST = "manifests/c09-rationale_manifest.yaml"


def _resolves(reqs, python_version):
    return 0, "Resolved 42 packages in 0.5s"


def _conflicts(reqs, python_version):
    return 1, "ERROR: ResolutionImpossible\n  jinja2"


def _manifest(tmp_path, **pipeline):
    """A minimal manifest pointing at the real 64-case eval set."""
    path = tmp_path / "m.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "t",
                "agent_module": "examples/multi_model_agents/agents/sonnet_agent",
                "eval_data": "examples/multi_model_agents/eval_data/eval_cases.yaml",
                "pairs": [{"id": "p", "model": "claude-sonnet-5", "system_prompt": "x"}],
                "pipeline": pipeline,
            }
        )
    )
    return str(path)


class TestItWarnsAndNeverBlocks:
    def test_an_underpowered_design_still_passes(self):
        """THE property that makes this a warning rather than a gate.

        Campaign 09's own manifest cannot resolve the effect it pre-registered,
        so this is not a hypothetical input. If it returned ok=False the CLI
        would exit 1, every campaign launch would hit it, and the check would
        be deleted or bypassed within a week.
        """
        result = design_sensitivity(C09_MANIFEST)
        assert result.ok is True
        assert "UNDERPOWERED" in result.detail

    def test_run_preflight_still_exits_clean_with_an_underpowered_manifest(self):
        """`run_preflight`'s caller exits 1 on any not-ok result."""
        results = run_preflight(manifest=C09_MANIFEST, resolver=_resolves)
        assert len(results) == 3
        assert all(r.ok for r in results)

    def test_a_real_dependency_conflict_still_fails(self):
        """The warning must not drown the check that actually blocks."""
        results = run_preflight(manifest=C09_MANIFEST, resolver=_conflicts)
        failed = [r for r in results if not r.ok]
        assert {r.name for r in failed} == {"agent requirements", "pipeline image pins"}


class TestTheDependencyChecksAreUntouched:
    def test_they_report_exactly_as_before_when_a_manifest_is_given(self):
        """Regressing dependency resolution to add a statistics warning would
        be a bad trade: campaign 07's arm died twice on a set that could not
        resolve, ~20 min into a GEAP build, reported only as 'Build failed'.
        """
        without = run_preflight(resolver=_resolves)
        with_manifest = run_preflight(manifest=C09_MANIFEST, resolver=_resolves)

        assert [(r.name, r.ok, r.detail) for r in without] == [
            (r.name, r.ok, r.detail) for r in with_manifest[:2]
        ]

    def test_with_no_manifest_the_mde_check_is_absent_not_fabricated(self):
        """No manifest means no case count and no `num_runs`. An MDE computed
        over a guessed design would be a number with nothing behind it.
        """
        results = run_preflight(resolver=_resolves)
        assert len(results) == 2
        assert MDE_CHECK_NAME not in {r.name for r in results}
        assert not any("campaign 09" in r.detail for r in results)


class TestItReadsTheManifestsActualDesign:
    def test_the_case_count_and_num_runs_come_from_the_manifest(self):
        """A design check that ignored the design would warn about a campaign
        nobody proposed -- and pass one that is genuinely underpowered.
        """
        assert manifest_design(C09_MANIFEST) == (64, 2)

    def test_num_runs_moves_with_the_pipeline_block(self, tmp_path):
        assert manifest_design(_manifest(tmp_path, num_runs=5)) == (64, 5)

    def test_num_runs_defaults_to_one_when_the_manifest_is_silent(self, tmp_path):
        """The pipeline's own default. Assuming the campaign 09 value would
        report a design better than the one that will actually run.
        """
        assert manifest_design(_manifest(tmp_path)) == (64, 1)

    def test_more_runs_resolve_a_smaller_effect(self, tmp_path):
        """If the detail did not move with `num_runs`, the block would be
        decorative -- the same warning for every manifest.
        """
        few = design_sensitivity(_manifest(tmp_path, num_runs=1))
        many = design_sensitivity(_manifest(tmp_path, num_runs=8))
        assert few.detail != many.detail
        assert "UNDERPOWERED" in few.detail
        assert few.detail.count("UNDERPOWERED") > many.detail.count("UNDERPOWERED")


class TestTheNumbersComeWithTheirProvenance:
    def test_the_detail_names_campaign_09_and_its_one_run_per_condition(self):
        """CLAUDE.md: a floor is not a property of a metric -- campaign 09's
        control drifted 9x what DOE 03 measured for the same one. An MDE
        printed without naming where its variance came from invites exactly
        that misreading, and this is the surface a reader sees.
        """
        detail = design_sensitivity(C09_MANIFEST).detail
        assert "campaign 09" in detail
        assert "one run per condition" in detail.lower()

    def test_the_numbers_reach_the_printed_output(self):
        """`render` prints detail only for failures. An advisory that passes
        silently tells nobody anything, which is the same as not running it.
        """
        printed = "\n".join(render(run_preflight(manifest=C09_MANIFEST, resolver=_resolves)))
        assert "safety_v1" in printed
        assert "campaign 09" in printed
        assert "PASS  design" not in printed, "an underpowered design must not read as a pass"


class TestItNeverBreaksTheCheckItRidesOn:
    def test_an_unreadable_eval_set_warns_instead_of_failing(self, tmp_path):
        """The MDE is a thermometer, and a broken thermometer must not kill the
        dependency resolution a campaign actually depends on. Same rule the
        canary follows: it records the problem and the stage continues.
        """
        path = tmp_path / "m.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "t",
                    "agent_module": "a",
                    "eval_data": "no/such/eval_cases.yaml",
                    "pairs": [{"id": "p", "model": "claude-sonnet-5", "system_prompt": "x"}],
                }
            )
        )
        result = design_sensitivity(str(path))
        assert result.ok is True
        assert "no/such/eval_cases.yaml" in result.detail
        assert all(r.ok for r in run_preflight(manifest=str(path), resolver=_resolves))


class TestTheCli:
    def test_preflight_with_a_manifest_prints_the_block_and_exits_zero(self, monkeypatch):
        monkeypatch.setattr("wrangler.tools.preflight._uv_resolver", _resolves)
        result = CliRunner().invoke(main, ["preflight", "--manifest", C09_MANIFEST])
        assert result.exit_code == 0, result.output
        assert "campaign 09" in result.output
        assert "safety_v1" in result.output

    def test_preflight_without_a_manifest_is_unchanged(self, monkeypatch):
        monkeypatch.setattr("wrangler.tools.preflight._uv_resolver", _resolves)
        result = CliRunner().invoke(main, ["preflight"])
        assert result.exit_code == 0, result.output
        assert "campaign 09" not in result.output

    def test_a_manifest_that_does_not_exist_is_rejected_by_the_cli(self, monkeypatch):
        """A typo'd path must not read as 'no manifest given, check skipped'."""
        monkeypatch.setattr("wrangler.tools.preflight._uv_resolver", _resolves)
        result = CliRunner().invoke(main, ["preflight", "--manifest", "nope.yaml"])
        assert result.exit_code != 0
