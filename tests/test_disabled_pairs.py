"""Tests for disabling a pair without deleting it from the manifest.

Opus cannot currently be deployed healthily: fifteen gated deploys across three
model versions and two prompts produced nothing above 50% reach, against a
concurrent control of four tiers at 93-100%
(docs/analysis/2026-09-01-opus-serving-failure.md). Any eval against it measures
dropout rather than the prompt.

Deleting the pair would lose the model id, the agent module and the reason.
Commenting it out loses the reason too, and a commented block rots. A parsed
`enabled: false` with a `disabled_reason` keeps the configuration honest and
makes re-enabling a one-line change.
"""

import yaml

from wrangler.core.factory import PairFactory


def _manifest(tmp_path, pairs):
    p = tmp_path / "m.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "m",
                "agent_module": "agents/x",
                "eval_data": "eval.yaml",
                "pairs": pairs,
            }
        )
    )
    return str(p)


def _pair(pid="p1", **kw):
    base = {"id": pid, "model": "gemini-3.5-flash", "system_prompt": "hi"}
    base.update(kw)
    return base


class TestParsing:
    def test_a_pair_is_enabled_by_default(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair()]))
        assert m.pairs[0].enabled is True
        assert m.pairs[0].disabled_reason == ""

    def test_enabled_false_is_parsed(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False)]))
        assert m.pairs[0].enabled is False

    def test_the_reason_travels_with_it(self, tmp_path):
        """A disabled pair with no reason is a mystery six months later."""
        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False, disabled_reason="0-50% reach")])
        )
        assert m.pairs[0].disabled_reason == "0-50% reach"

    def test_a_disabled_pair_is_still_loaded(self, tmp_path):
        """It stays in the manifest so the config is not lost."""
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in m.pairs] == ["p1", "p2"]


class TestFiltering:
    def test_a_disabled_pair_is_skipped_by_a_sweep(self, tmp_path):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in _filter_pairs(m, None)] == ["p2"]

    def test_the_skip_says_why(self, tmp_path, capsys):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False, disabled_reason="cannot serve")])
        )
        _filter_pairs(m, None)
        out = capsys.readouterr().out
        assert "p1" in out
        assert "cannot serve" in out

    def test_naming_a_disabled_pair_explicitly_still_runs_it(self, tmp_path):
        """`--pair opus` is a deliberate act; the flag should not be silently ignored."""
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False)]))
        assert [p.id for p in _filter_pairs(m, "p1")] == ["p1"]

    def test_all_pairs_disabled_yields_nothing_rather_than_everything(self, tmp_path):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False), _pair("p2", enabled=False)])
        )
        assert _filter_pairs(m, None) == []


class TestExperimentExcludesDisabledPairs:
    """The phantom-arm bug: a disabled pair used to get a results row anyway.

    `Experiment.create` used to copy `manifest.pairs` (the raw list)
    verbatim into `config["pairs"]`, and `enabled`/`disabled_reason` were not
    among the fields that dict carried -- so a disabled pair survived into
    every experiment as if it were a normal arm, with `model: ""` and empty
    before/after scores once eval skipped it. That row fed the cost-benefit
    table, the ordering, and the analyzer's noise-floor logic.

    The fix carries `enabled`/`disabled_reason` into config.yaml (see
    `TestExplicitPairStillOverridesADisabledOne` below for why they have to
    travel rather than being dropped at creation) and filters them back out
    only in `Experiment.pair_ids`, which is what a report, gate, or tracking
    entry reads.
    """

    def _experiment(self, tmp_path):
        (tmp_path / "agents" / "x").mkdir(parents=True)
        eval_path = tmp_path / "eval.yaml"
        eval_path.write_text(yaml.safe_dump([{"query": "hi", "expected_response": "hi"}]))
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(
            yaml.safe_dump(
                {
                    "name": "m",
                    "agent_module": "agents/x",
                    "eval_data": "eval.yaml",
                    "pairs": [
                        _pair("kept"),
                        _pair("opus", enabled=False, disabled_reason="0-50% reach"),
                    ],
                }
            )
        )
        from wrangler.orchestration.experiment import Experiment

        return Experiment.create(manifest_path, name="exp", base_dir=tmp_path / "experiments")

    def test_disabled_pair_is_absent_from_pair_ids(self, tmp_path):
        exp = self._experiment(tmp_path)
        assert exp.pair_ids == ["kept"]

    def test_disabled_pair_is_absent_from_a_generated_report(self, tmp_path, monkeypatch):
        exp = self._experiment(tmp_path)

        captured = {}
        monkeypatch.setattr(
            "wrangler.orchestration.stages._generate_report",
            lambda results, name, **kw: captured.update(results=results),
        )

        from wrangler.orchestration.stages import stage_report

        stage_report(exp, use_paperbanana=False)

        assert "opus" not in captured["results"]
        assert "kept" in captured["results"]


class TestExplicitPairStillOverridesADisabledOne:
    """`wrangler run manifest.yaml --pair opus` must still reach opus.

    The first fix for the phantom-arm bug dropped disabled pairs from
    `config["pairs"]` entirely at `Experiment.create` time. That closed the
    phantom-row bug but broke the other half of the same requirement: every
    stage function calls `_filter_pairs(exp.manifest, pair_id)`, and with the
    disabled pair gone from config.yaml there was nothing left for a named
    `--pair opus` to find -- `_filter_pairs` raises `KeyError` via
    `manifest.get_pair()` instead of running it. `enabled`/`disabled_reason`
    must survive into config.yaml so the raw pair is still there for an
    explicit override, even though `pair_ids` (unfiltered sweeps, reports,
    gates) excludes it.
    """

    def test_naming_a_disabled_pair_reaches_it_through_the_experiment(self, tmp_path):
        (tmp_path / "agents" / "x").mkdir(parents=True)
        (tmp_path / "eval.yaml").write_text(
            yaml.safe_dump([{"query": "hi", "expected_response": "hi"}])
        )
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(
            yaml.safe_dump(
                {
                    "name": "m",
                    "agent_module": "agents/x",
                    "eval_data": "eval.yaml",
                    "pairs": [
                        _pair("kept"),
                        _pair("opus", enabled=False, disabled_reason="0-50% reach"),
                    ],
                }
            )
        )
        from wrangler.orchestration.experiment import Experiment
        from wrangler.orchestration.stages import _filter_pairs

        exp = Experiment.create(manifest_path, name="exp", base_dir=tmp_path / "experiments")

        # The disabled pair is still reachable by name on the reconstructed
        # manifest -- this is what every stage function calls with pair_id
        # threaded from `--pair`.
        selected = _filter_pairs(exp.manifest, "opus")
        assert [p.id for p in selected] == ["opus"]

        # And an unfiltered sweep still skips it -- the other half of the
        # same requirement, unchanged by this fix.
        assert [p.id for p in _filter_pairs(exp.manifest, None)] == ["kept"]


class TestOpusIsActuallyDisabled:
    """The point of the exercise. Named so re-enabling is a deliberate diff."""

    def test_the_example_manifest_disables_opus(self):
        m = PairFactory.load("examples/multi_model_agents/manifest.yaml")
        opus = [p for p in m.pairs if "opus" in p.id.lower()]
        assert opus, "premise: the example manifest still has an opus pair"
        for p in opus:
            assert p.enabled is False, f"{p.id} should be disabled"
            assert p.disabled_reason, f"{p.id} needs a reason"


class TestBothSweepPathsRespectIt:
    """Local and pipeline are separate code paths and only one filtered.

    `stage_deploy` went through `_filter_pairs`; `deploy_pipeline` read
    `manifest.pairs` directly, so `wrangler pipeline run` would have deployed
    and evaluated opus after it was disabled everywhere else. Same shape as the
    health gate covering one of three deploy paths.
    """

    def test_enabled_pairs_excludes_disabled(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in m.enabled_pairs] == ["p2"]
        assert [p.id for p in m.pairs] == ["p1", "p2"], "raw list still holds everything"

    def test_the_pipeline_path_uses_enabled_pairs(self):
        from pathlib import Path

        src = Path("wrangler/pipeline/deploy_pipeline.py").read_text()
        assert "manifest.enabled_pairs" in src
        # Any remaining bare `manifest.pairs` must only be counting the total
        # for the "N disabled" message, never selecting what to run.
        for line in src.splitlines():
            if "manifest.pairs" in line and "enabled_pairs" not in line:
                assert "len(" in line, f"pipeline selects from raw pairs: {line.strip()}"

    def test_the_local_path_uses_the_filter(self):
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def _filter_pairs")
        assert "pair.enabled" in src[i : i + 900]
